import requests
import json
import re
import random
import shutil
import os
import tempfile
import urllib.parse
from social.constants import *
from ai.hugging import HuggingFaceAI
from ai.providers import ai_client
from social.models import SocialPost, SocialComment
from inventory.models import Property, PropertyImage
from inventory.utils import all_images_gone, is_permanently_gone
from django.db.models import Max
import time
from django.conf import settings
import os
import shutil
import requests
import tempfile
from moviepy.video.VideoClip import ImageClip
from moviepy import (
    ImageClip,
    concatenate_videoclips,
    TextClip,
    CompositeVideoClip,
    VideoFileClip,
    ColorClip,
    vfx,
)
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
import numpy as np
from PIL import Image
from social.content.cards import ACCENT, FG
from social.content.listing_cards import (
    MARGIN as CARD_MARGIN,
    _details_line,
    _photo_band,
)
from membership.utils import notify_social_token_expired
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

# When a reel's video fails to encode (e.g. ffmpeg OOM-killed on the VPS), the
# poster moves on to the next candidate property rather than aborting the run.
# Cap the attempts so a bad batch doesn't churn through many heavy encodes.
MAX_REEL_ATTEMPTS = 3

# A candidate whose source listing has been taken down costs only a few 404s —
# nothing like an ffmpeg encode — so it must NOT consume a MAX_REEL_ATTEMPTS
# slot. Without this the poster deadlocks: the queue orders never-posted
# properties first, so a handful of delisted listings sit at the head and burn
# every attempt on every run, forever. Bound the skipping anyway so one run
# can't walk the whole table doing HTTP.
MAX_DELISTED_SKIPS = 20


class _NoLiveImages:
    """Falsy sentinel: every photo on the source listing is gone (404).

    Falsy so existing `if create_property_video(...)` truth-checks still read
    as "no video was made", while callers that care can tell this cheap skip
    apart from an expensive encode failure.
    """

    __slots__ = ()

    def __bool__(self):
        return False


NO_LIVE_IMAGES = _NoLiveImages()


def refresh_access_token():
    """Renew the stored Page token from the long-lived user token in .env.

    The seed (PAGE_ACCESS_TOKEN) is a long-lived FB *user* token; /me/accounts
    exchanges it for the Page token, which — derived from a long-lived user
    token — does not expire. We pick the page matching PAGE_ID rather than
    blindly taking data[0], so a multi-page account can't grab the wrong one.
    Run on a cron so the token in social_access_token.json stays valid.
    """
    def save_token(token):
        with open("social_access_token.json", "w") as f:
            json.dump({"access_token": token}, f)

    # Read at call time so a fresh .env value is picked up without a code change.
    seed_token = os.getenv("PAGE_ACCESS_TOKEN", "") or PAGE_ACCESS_TOKEN
    if not seed_token:
        logger.error(
            "PAGE_ACCESS_TOKEN (long-lived user token) is not set in .env; "
            "cannot refresh the Page access token."
        )
        return None

    url = "https://graph.facebook.com/v19.0/me/accounts/"
    response = requests.get(url, params={"access_token": seed_token})
    if response.status_code != 200:
        logger.error(f"Failed to refresh access token: {response.json()}")
        return None

    pages = response.json().get("data", [])
    page = next((p for p in pages if str(p.get("id")) == str(PAGE_ID)), None)
    if page is None:
        page = pages[0] if pages else None
    if not page or not page.get("access_token"):
        logger.error(
            f"No Page access token for PAGE_ID={PAGE_ID} in /me/accounts response: "
            f"{response.json()}"
        )
        return None

    save_token(page["access_token"])
    logger.info("Access token refreshed successfully.")
    return page["access_token"]


def get_fresh_token():
    try:
        with open("social_access_token.json", "r") as f:
            return json.load(f)["access_token"]
    except (FileNotFoundError, KeyError):
        return None


def prepare_image_url_for_facebook(image_url):
    image_url = image_url.lstrip("/")
    # Decode the URL twice
    decoded_once = urllib.parse.unquote(image_url)
    decoded_final = urllib.parse.unquote(decoded_once)

    # Ensure the URL starts with 'https://'
    if decoded_final.startswith("media/https:/"):
        image_url = decoded_final.replace("media/https:/", "https://", 1)
    elif decoded_final.startswith("https:/"):
        image_url = decoded_final.replace("https:/", "https://", 1)

    return image_url


def _download_image_to_tempfile(url):
    """Download remote image to a temp file with .jpg extension."""
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, stream=True, headers=headers)
    response.raise_for_status()

    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
    with open(tmp_file.name, "wb") as f:
        for chunk in response.iter_content(1024):
            f.write(chunk)
    return tmp_file.name


def _remaining_images_gone(property_id: int, already_checked) -> bool:
    """Confirm the property's OTHER photos are gone before we retire it.

    The reel builder only samples the first few images, so check the rest
    here — a property whose later photos still load is merely skipped.
    """
    remaining = PropertyImage.objects.filter(property_id=property_id).exclude(
        pk__in=[img.pk for img in already_checked]
    )
    return all_images_gone(
        prepare_image_url_for_facebook(img_obj.file.url) for img_obj in remaining
    )


def _get_random_mp3_full_path(exclude: str) -> str:
    folder_path = os.path.join(settings.STATIC_ROOT, "audios_for_social_posts")

    mp3_files = [
        f for f in os.listdir(folder_path) if f.endswith(".mp3") and f != exclude
    ]
    if not mp3_files:
        return None

    return os.path.join(folder_path, random.choice(mp3_files))


def _sanity_check_ai_caption(ai_caption: str) -> str:
    # Strip wrapping quotes and tidy whitespace. We intentionally do NOT trim
    # everything after the last '.!?' anymore: CTAs often end in an emoji (no
    # terminal punctuation), and the old behaviour silently deleted them.
    ai_caption = ai_caption.replace('"', "").strip()
    # Collapse 3+ blank lines down to a single blank line.
    ai_caption = re.sub(r"\n{3,}", "\n\n", ai_caption)
    return ai_caption.strip()


def _clean_location(location: str) -> str:
    """Tidy a scraped location string for display.

    Strips trailing scraper artefacts like '[ ■ Surrounding environment ]'
    and bracketed notes, collapses whitespace.
    """
    if not location:
        return ""
    # Drop anything from a '[' onward (scraper section markers) and any
    # leftover '■'/'●' bullet glyphs.
    location = re.split(r"[\[■●]", location)[0]
    return re.sub(r"\s+", " ", location).strip(" ,-")


def _clean_area(area: str) -> str:
    """Normalise a scraped area string for a caption.

    '198.73m 2 （60.11坪）（登記）'  -> '198.73 m² (60.11 tsubo)'
    '101㎡ (public book)'          -> '101 m²'
    '103.24㎡ (crystal)'           -> '103.24 m²'

    The parentheticals are Japanese measurement-basis notes that the scraper
    machine-translated, some of them badly: 公簿 (registered area) comes through
    as "public book", 内法 (inside-wall measurement) as "internal method", and
    実測 (actual measurement) as either "actual measurement" or, memorably,
    "crystal". They are noise in an Instagram caption whichever way they were
    translated, so all of them go.

    Rather than listing the mistranslations — the scraper will invent new ones —
    any parenthetical without a digit in it is dropped. The one worth keeping,
    the tsubo figure, always carries a number.
    """
    if not area:
        return ""
    area = str(area)

    # Units first, so a converted 坪 figure keeps its digits and survives the
    # parenthetical cull below.
    area = re.sub(r"㎡|m\s*2\b|m\s*²", "m²", area)
    # （60.11坪） -> (60.11 tsubo). '坪' is also scraped as the mistranslated
    # "ping", so both spellings are converted.
    area = re.sub(
        r"[（(]\s*([\d.]+)\s*(?:坪|ping)\s*[）)]", r"(\1 tsubo)", area, flags=re.I
    )

    # Scraper tail seen on ~30 listings: 'has a total of 3/4 points'. Carries
    # digits, so it needs removing by name before the digit-free rule runs.
    area = re.sub(r"\bhas a total of\s*[\d/]+\s*points?\b", "", area, flags=re.I)

    # Any remaining parenthetical with no digit in it is a measurement-basis
    # note, whatever it was translated to.
    area = re.sub(r"[（(][^\d（）()]*[）)]", "", area)

    area = re.sub(r"\s+", " ", area)
    # One space before the unit: '101m²' -> '101 m²'.
    area = re.sub(r"([\d.])\s*m²", r"\1 m²", area)
    return area.strip(" ,-")


def _location_hashtags(location: str) -> list:
    """Derive location-aware hashtags (prefecture + city) from a location."""
    tags = []
    if not location:
        return tags
    for pref in JAPAN_PREFECTURES:
        if re.search(rf"\b{pref}\b", location, re.IGNORECASE):
            tags.append(f"#{pref.lower()}")
            break
    city = re.search(r"([A-Za-z][A-Za-z]+)\s+City", location)
    if city:
        tags.append(f"#{city.group(1).lower()}")
    return tags


def build_hashtags(location: str = "") -> str:
    """Core tags + location-aware tags + a sampled set of rotating tags.

    Consistent, relevant count (no more random 1..19), deduped, order-stable.
    """
    chosen = list(CORE_HASHTAGS)
    for tag in _location_hashtags(location):
        if tag not in chosen:
            chosen.append(tag)

    pool = [t for t in ROTATING_HASHTAGS if t not in chosen]
    k = min(NUM_ROTATING_HASHTAGS, len(pool))
    chosen += random.sample(pool, k) if k else []
    return " ".join(chosen)


def select_properties_to_post(posts_queryset, price_limit, limit=None):
    """Properties with images under price_limit, cheapest first.

    Eligibility matches the homepage grid (show_in_front=True, price in
    (0, price_limit]) plus the social-only requirement of at least one image
    — we can't build a reel/post without a photo. Featured is NOT required;
    it's only a tiebreaker.

    Ordering: never-posted properties get a turn before anything is reposted,
    then a featured listing that is also cheap, then the least-recently-posted
    (to keep the rotation fair), then cheapest first — which, since everything
    never posted has the same empty posting history, is what actually decides
    the queue day to day.

    Featured used to be the first key, on the reasoning that the social feed
    should mirror the home page grid. On the home page that ordering costs
    nothing: every listing is on the page, featured ones are simply at the top.
    A queue is not a page. One featured 1400万 listing sat at the head of it
    ahead of 1,558 never-posted properties, kept sitting there after it had been
    posted, and would have been next on every run for the rest of the year.
    Featured now boosts a listing once, and only under
    FEATURED_BOOST_PRICE_LIMIT.

    `posts_queryset` is the SocialPost rows for the relevant channel; matching
    is by property_url == Property.url (same value written when a post is made).
    """
    rows = posts_queryset.values("property_url").annotate(last=Max("datetime"))
    last_posted = {r["property_url"]: r["last"] for r in rows}

    candidates = list(
        Property.objects.filter(
            show_in_front=True,
            images__isnull=False,
            price__gt=0,
            price__lte=price_limit,
        ).distinct()
    )
    def _boosted(property):
        """Featured *and* cheap. Featured alone is a home page decision."""
        return bool(property.featured) and (
            (property.price or 0) <= FEATURED_BOOST_PRICE_LIMIT
        )

    # Sort key, in priority order:
    #   1. already-posted?    never-posted ahead of posted, so the boost below
    #                         is worth exactly one turn and cannot repeat
    #   2. not boosted        a cheap featured listing leads its group
    #   3. last-posted-time   oldest repost first (only separates the posted
    #                         group — everything never posted ties at 0 here)
    #   4. price              cheapest first
    candidates.sort(
        key=lambda p: (
            last_posted.get(p.url) is not None,
            not _boosted(p),
            last_posted.get(p.url) or 0,
            p.price or 0,
        )
    )
    return candidates[:limit] if limit else candidates


def generate_caption_for_post(
    property_location: str,
    property_url: str,
    property_price: float,
    property_building_area: str,
    property_land_area: str,
    last_caption_generated: str,
    use_ai_caption: bool,
):
    # Tidy the scraped fields before they go anywhere near the caption.
    location = _clean_location(property_location)
    building_area = _clean_area(property_building_area)
    land_area = _clean_area(property_land_area)
    hashtags = build_hashtags(location)

    # Instagram truncates the caption at roughly 125 characters, and what used
    # to sit in those characters was a soft lifestyle sentence while the price
    # waited below the fold. The price and the place are the reason anyone stops
    # scrolling on an akiya, so they go first and the copy follows.
    lead = " · ".join(part for part in (str(property_price), location) if part)

    def _details_block():
        # No longer repeats the price and location: they are the lead line now,
        # and saying them twice in one caption reads like a template.
        lines = []
        if building_area:
            lines.append(f"🏡 Building: {building_area}")
        if land_area:
            lines.append(f"🌳 Land: {land_area}")
        lines.append(f"🔗 www.akiyainjapan.com{property_url}")
        return "\n".join(lines) + f"\n\n{hashtags}"

    ai_caption = ""
    selected_angle = ""
    if use_ai_caption:
        try:
            llm = ai_client()

            # Vary the angle so a feed of posts doesn't read identically.
            caption_angles = [
                "lead with the lifestyle this location offers",
                "lead with the value/affordability for the price",
                "lead with the dream of owning a home in rural Japan",
                "lead with what makes this area or region special",
                "lead with the renovation/creative potential",
                "lead with a vivid sense of place and the seasons",
            ]
            cta_options = [
                "Full details and more photos on our website 👇",
                "DM us if you'd like to know more 💬",
                "Save this one and check the link in our bio ✨",
                "More photos and the full listing on our site 🏠",
                "Thinking about it? Let's chat — drop us a message 📩",
            ]
            selected_angle = random.choice(caption_angles)
            selected_cta = random.choice(cta_options)

            ai_caption = llm.generate_text(
                prompt=(
                    "You write Instagram/Facebook captions for a brand that sells "
                    "affordable houses (akiya) in Japan to an international audience.\n\n"
                    f"Property location: {location}\n"
                    f"Price: {property_price}\n\n"
                    "Write ONE caption with this structure:\n"
                    "1. A short, scroll-stopping hook (one line).\n"
                    "2. Two or three short, warm sentences that paint the lifestyle "
                    "and sense of place. Reference the actual location/region.\n"
                    f"3. End with this exact call-to-action: {selected_cta}\n\n"
                    f"Creative direction: {selected_angle}.\n"
                    "Rules:\n"
                    "- Use line breaks between the hook, the body, and the CTA.\n"
                    "- Sound human and specific; avoid clichés like 'nestled', "
                    "'hidden gem', 'hustle and bustle', 'boasts', 'slip away'.\n"
                    "- At most 1-2 tasteful emojis in the body.\n"
                    "- Do NOT invent features (bedrooms, condition, views) you weren't given.\n"
                    "- Do NOT include hashtags, the price, or the address: the caption "
                    "already opens with a line stating the price and the location, so "
                    "repeating either reads like a template.\n"
                    f"- Do NOT repeat this previous caption: {last_caption_generated}\n"
                    "Output ONLY the caption text."
                )
            )

            ai_caption = _sanity_check_ai_caption(ai_caption)
            caption = f"{lead}\n\n{ai_caption}\n\n{_details_block()}"
            logger.info(f"Caption generated via AI: {caption}")
        except Exception as e:
            # The lead line is built here, not by the model, so a dead AI still
            # produces a price-led caption rather than a bare details block.
            caption = f"{lead}\n\n{_details_block()}"
            selected_angle = ""
            logger.error(f"AI caption generation failed: {e}")
        return ai_caption, caption, selected_angle
    else:
        logger.info("AI caption generation is disabled, using default caption format.")
        return ai_caption, f"{lead}\n\n{_details_block()}", ""


LISTING_CARD_DIR = os.path.join(settings.MEDIA_ROOT, "social_cards")


def _card_location(property: Property, max_chars: int = 70) -> str:
    """A place name fit to be burnt into an image: Latin script, short, specific.

    The scraped address is the specific thing, but it arrives with Japanese
    characters in it and Montserrat has no CJK glyphs — those render as empty
    boxes, which looks worse than saying less. So each candidate is stripped to
    Latin script and the first one with anything left wins: the address, then
    the inferred prefecture, then nothing at all. A card with no place on it is
    a poorer card; a card with three empty boxes on it looks broken.
    """
    for candidate in (property.display_location, property.get_location_for_front()):
        latin = re.sub(
            r"\s+", " ",
            re.sub(r"[^\x00-\x7F]+", " ", _clean_location(candidate or "")),
        ).strip(" ,-")
        if len(latin) >= 3:
            if len(latin) > max_chars:
                # Cut on a word so it never ends mid-place-name. The reel asks
                # for fewer characters than the card: moviepy raises when text
                # overflows its box, and that costs every overlay on the video.
                latin = latin[:max_chars].rsplit(" ", 1)[0]
            return latin.strip(" ,-")
    return ""


def _listing_card_urls(property: Property):
    """Public URLs for the branded carousel, or None to fall back to raw photos.

    None rather than an empty list on failure, so the caller can tell "post the
    photos plain" from "there is nothing to post". Nothing in here is allowed to
    raise: this runs from cron, and an unbranded post is a far better outcome
    than a post that never went out.
    """
    from social.content.hosting import CARD_SUBDIR, public_url_for_card
    from social.content.listing_cards import prune_old_cards, render_listing_cards

    temp_paths = []
    try:
        for image in property.get_ordered_images()[:LISTING_CARDS_MAX_PHOTOS]:
            raw_url = prepare_image_url_for_facebook(image.file.url)
            try:
                temp_paths.append(_download_image_to_tempfile(raw_url))
            except Exception as exc:
                # A 404 here is usually a delisted listing; the reel pipeline
                # owns that judgement, so just draw with what did load.
                logger.warning(f"Skipping photo {raw_url} for card render: {exc}")

        if not temp_paths:
            logger.warning("No photo could be downloaded; not rendering cards.")
            return None

        card_paths = render_listing_cards(
            temp_paths,
            price=property.get_price_for_front,
            location=_card_location(property),
            building_area=_clean_area(property.building_area),
            land_area=_clean_area(property.land_area),
            link=f"www.akiyainjapan.com{property.get_public_url}",
            out_dir=LISTING_CARD_DIR,
            slug=f"listing-{property.pk}-{time.strftime('%Y%m%d%H%M%S')}",
            add_summary=LISTING_CARDS_ADD_SUMMARY,
        )
        if not card_paths:
            return None

        urls = [url for url in (public_url_for_card(p) for p in card_paths) if url]
        if not urls:
            logger.error("Cards rendered but none could be made publicly fetchable.")
            return None

        # Housekeeping after the fact, never before: a prune that throws must
        # not cost us the post it just rendered.
        for directory in (
            LISTING_CARD_DIR, os.path.join(settings.STATIC_ROOT, CARD_SUBDIR)
        ):
            try:
                prune_old_cards(directory, "listing-", LISTING_CARDS_KEEP_DAYS)
            except Exception as exc:
                logger.warning(f"Card prune skipped for {directory}: {exc}")
        return urls
    except Exception as exc:
        logger.error(f"Listing card render failed, posting raw photos: {exc}")
        return None
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except OSError:
                pass


def _listing_media_urls(property: Property):
    """What a listing post should actually upload, branded if we can manage it."""
    if LISTING_CARDS_ENABLED:
        urls = _listing_card_urls(property)
        if urls:
            return urls
    return [
        prepare_image_url_for_facebook(image.file.url)
        for image in property.get_ordered_images()[:5]
    ]


def post_to_instagram(
    property: Property, last_caption_generated: str, use_ai_caption: bool
):
    property_image_urls = _listing_media_urls(property)

    ai_caption, caption, caption_angle = generate_caption_for_post(
        property_location=property.location,
        property_url=property.get_public_url,
        property_price=property.get_price_for_front,
        property_building_area=property.building_area,
        property_land_area=property.land_area,
        last_caption_generated=last_caption_generated,
        use_ai_caption=use_ai_caption,
    )

    media_ids = []

    # Upload each image as a carousel item. The URLs arrive ready to fetch —
    # _listing_media_urls has already prepared them, and running the raw-photo
    # fixups over one of our own card URLs would mangle it.
    for image_url in property_image_urls:
        upload_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media"
        payload = {
            "image_url": image_url,
            "is_carousel_item": True,
            "access_token": get_fresh_token(),
        }
        response = requests.post(upload_url, data=payload)
        result = response.json()

        if "id" in result:
            logger.info(f"Uploaded image for carousel: {image_url}")
            media_ids.append(result["id"])
        else:
            logger.error(f"Failed to upload image: {result}")

    # Create carousel container with the uploaded media IDs
    if media_ids:
        carousel_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media"
        payload = {
            "media_type": "CAROUSEL",
            "children": ",".join(media_ids),
            "caption": caption,
            "access_token": get_fresh_token(),
        }

        carousel_response = requests.post(carousel_url, data=payload)
        result = carousel_response.json()

        if "id" in result:
            creation_id = result["id"]
            publish_url = (
                f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media_publish"
            )
            publish_payload = {
                "creation_id": creation_id,
                "access_token": get_fresh_token(),
            }
            publish_response = requests.post(publish_url, data=publish_payload)
            if publish_response.status_code == 200:
                logger.info("Successfully posted carousel to Instagram.")
                SocialPost.objects.create(
                    ai_caption=ai_caption,
                    caption=caption,
                    caption_angle=caption_angle,
                    # The published id, kept so insights can be read back later.
                    # Discarding it is what made every earlier post
                    # unattributable, including retrospectively.
                    media_id=str(publish_response.json().get("id") or ""),
                    property_url=property.url,
                    social_media="instagram",
                )
            else:
                logger.error(f"Failed to publish carousel: {publish_response.json()}")
        else:
            logger.error(
                f"Failed to create carousel container: {carousel_response.json()}"
            )
    else:
        logger.warning("No images were uploaded; skipping Instagram post.")


def post_to_facebook(
    property: Property, last_caption_generated: str, use_ai_caption: bool
):
    property_image_urls = _listing_media_urls(property)

    ai_caption, caption, caption_angle = generate_caption_for_post(
        property_location=property.location,
        property_url=property.get_public_url,
        property_price=property.get_price_for_front,
        property_building_area=property.building_area,
        property_land_area=property.land_area,
        last_caption_generated=last_caption_generated,
        use_ai_caption=use_ai_caption,
    )

    # Upload each image (unpublished). Already-prepared URLs — see the note in
    # post_to_instagram.
    media_fbids = []
    for image_url in property_image_urls:
        upload_url = f"https://graph.facebook.com/v19.0/{PAGE_ID}/photos"
        payload = {
            "url": image_url,
            "published": "false",
            "access_token": get_fresh_token(),
        }
        response = requests.post(upload_url, data=payload)
        result = response.json()

        if response.status_code == 200 and "id" in result:
            logger.info(f"Uploaded image: {image_url}")
            media_fbids.append(result["id"])
        else:
            logger.error(f"Failed to upload image: {result}")

    # Create the post with all attached media
    if media_fbids:
        post_url = f"https://graph.facebook.com/v19.0/{PAGE_ID}/feed"
        payload = {
            "message": caption,
            "access_token": get_fresh_token(),
        }
        for i, media_id in enumerate(media_fbids):
            payload[f"attached_media[{i}]"] = f'{{"media_fbid":"{media_id}"}}'

        response = requests.post(post_url, data=payload)
        result = response.json()

        if response.status_code == 200:
            logger.info("Successfully posted to Facebook with multiple images.")
            SocialPost.objects.create(
                ai_caption=ai_caption,
                caption=caption,
                caption_angle=caption_angle,
                media_id=str(result.get("id") or ""),
                property_url=property.url,
                social_media="facebook",
            )
        else:
            logger.error(f"Failed to create post: {result}")
    else:
        logger.warning("No images were uploaded; skipping Facebook post.")


_UPLOAD_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def _upload_to_catbox(filepath: str):
    with open(filepath, "rb") as f:
        r = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": f},
            headers={"User-Agent": _UPLOAD_UA},
            timeout=120,
        )
    if r.status_code == 200 and r.text.startswith("https://"):
        return r.text.strip()
    raise RuntimeError(f"catbox: {r.status_code} {r.text[:120]}")


def _upload_to_0x0(filepath: str):
    with open(filepath, "rb") as f:
        r = requests.post(
            "https://0x0.st",
            files={"file": f},
            headers={"User-Agent": _UPLOAD_UA},
            timeout=120,
        )
    if r.status_code == 200 and r.text.startswith("https://"):
        return r.text.strip()
    raise RuntimeError(f"0x0.st: {r.status_code} {r.text[:120]}")


def _upload_to_transfersh(filepath: str):
    name = os.path.basename(filepath)
    with open(filepath, "rb") as f:
        r = requests.put(
            f"https://transfer.sh/{name}",
            data=f,
            headers={"User-Agent": _UPLOAD_UA, "Max-Days": "3"},
            timeout=120,
        )
    if r.status_code == 200 and r.text.startswith("https://"):
        return r.text.strip()
    raise RuntimeError(f"transfer.sh: {r.status_code} {r.text[:120]}")


def _upload_video(filepath: str):
    """Try a few free public file hosts in order. Datacenter IPs get rejected
    by some of them (catbox returns 412 'Invalid uploader'), so we have a
    fallback chain.
    """
    for uploader in (_upload_to_catbox, _upload_to_0x0, _upload_to_transfersh):
        try:
            url = uploader(filepath)
            logger.info(f"Uploaded video via {uploader.__name__}: {url}")
            return url
        except Exception as exc:
            logger.warning(f"{uploader.__name__} failed: {exc}")
    return None


def post_instagram_reel():
    try:
        instagram_reels = SocialPost.objects.filter(
            social_media="instagram", content_type="reel"
        )
        last_reel_posted_sound_track = (
            instagram_reels.order_by("-datetime").first().sound_track
            if instagram_reels
            else None
        )
        last_caption_generated = (
            instagram_reels.order_by("-datetime").first().ai_caption
            if instagram_reels
            else None
        )

        # Never-posted first, then least-recently-posted — avoids reposting the
        # same reel over and over once the eligible inventory is exhausted.
        candidates = select_properties_to_post(instagram_reels, PRICE_LIMIT_INSTAGRAM)

        if not candidates:
            logger.warning("No suitable property found to post on Instagram Reels.")
            return

        audio_path = _get_random_mp3_full_path(exclude=last_reel_posted_sound_track)

        # Try candidates until one produces a video. A failed encode logs and
        # moves on instead of aborting the whole run.
        property_to_post_instagram_reel = None
        # Defined before the loop so the row written at the end can read it even
        # if the first candidate is the one that works.
        video_meta = {}
        encode_attempts = delisted_skips = 0
        for candidate in candidates:
            if encode_attempts >= MAX_REEL_ATTEMPTS or delisted_skips >= MAX_DELISTED_SKIPS:
                break
            video_meta.clear()
            result = create_property_video(
                candidate.pk,
                output_path="property_video.mp4",
                audio_path=audio_path,
                duration_per_image=3,
                meta=video_meta,
            )
            if result:
                property_to_post_instagram_reel = candidate
                break
            if result is NO_LIVE_IMAGES:
                delisted_skips += 1
                logger.warning(
                    f"Skipping delisted property {candidate.url}; trying next "
                    "(does not count as a video attempt)."
                )
                continue
            encode_attempts += 1
            logger.warning(
                f"Skipping property {candidate.url}: video creation failed, trying next."
            )

        if not property_to_post_instagram_reel:
            logger.error(
                "Could not create a video for any candidate property; nothing posted to Instagram Reels."
            )
            return

        media_dir = os.path.join(settings.MEDIA_ROOT, "generated_videos")
        os.makedirs(media_dir, exist_ok=True)
        target_path = os.path.join(media_dir, "property_video.mp4")
        shutil.move("property_video.mp4", target_path)

        # Serve the video directly from the site (file was just written to
        # MEDIA_ROOT/generated_videos/). We don't probe from the same host —
        # shared hosting loopback-routes akiyainjapan.com to the local
        # LiteSpeed (which still has the old shared cert) and trips a false
        # cert-mismatch. Instagram fetches from outside through Cloudflare
        # where the cert is valid, so just trust the URL.
        video_url = "https://akiyainjapan.com/media/generated_videos/property_video.mp4"
        logger.info(f"Video URL for Instagram fetch: {video_url}")

        ai_caption, caption, caption_angle = generate_caption_for_post(
            property_to_post_instagram_reel.location,
            property_to_post_instagram_reel.get_public_url,
            property_to_post_instagram_reel.get_price_for_front,
            property_to_post_instagram_reel.building_area,
            property_to_post_instagram_reel.land_area,
            last_caption_generated=last_caption_generated,
            use_ai_caption=USE_AI_CAPTION,
        )

        # Step 1: Create media container
        media_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media"
        media_payload = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            # Publishing into the Reels tab alone gave up the feed and the
            # profile grid for nothing — same video, less shelf space.
            "share_to_feed": REEL_SHARE_TO_FEED,
            "access_token": get_fresh_token(),
        }
        media_response = requests.post(media_url, data=media_payload)
        logger.info("Media upload response: " + media_response.text)

        time.sleep(180)
        if "id" in media_response.json():
            creation_id = media_response.json()["id"]

            # Step 2: Publish the video
            publish_url = (
                f"https://graph.facebook.com/v19.0/{INSTAGRAM_USER_ID}/media_publish"
            )
            publish_payload = {
                "creation_id": creation_id,
                "access_token": get_fresh_token(),
            }
            publish_response = requests.post(publish_url, data=publish_payload)
            logger.info("Publish response: " + publish_response.text)

            if publish_response.status_code == 200:
                media_id = publish_response.json().get("id")
                logger.info("✅ Successfully posted to Instagram Reels!")

                # Step 3: Add comment to the published Reel
                if media_id:
                    comment_payload = {
                        "message": DEFAULT_COMMENT,
                        "access_token": get_fresh_token(),
                    }
                    comment_url = (
                        f"https://graph.facebook.com/v19.0/{media_id}/comments"
                    )
                    comment_response = requests.post(comment_url, data=comment_payload)
                    logger.info("Comment response: " + comment_response.text)

                # Log the post. media_id, the caption angle and the burnt-in
                # hook are what make this row answerable later: which of the six
                # angles and which hook actually travelled.
                SocialPost.objects.create(
                    ai_caption=ai_caption,
                    caption=caption,
                    caption_angle=caption_angle,
                    overlay_hook=video_meta.get("overlay_hook", ""),
                    media_id=str(media_id or ""),
                    property_url=property_to_post_instagram_reel.url,
                    social_media="instagram",
                    content_type="reel",
                    sound_track=audio_path,
                )
            else:
                logger.error("Failed to publish Reel: " + publish_response.text)
        else:
            logger.error("Failed to create media container: " + media_response.text)
    except Exception as e:
        logger.error(f"Error posting Instagram Reel: {e}")
        notify_social_token_expired(message=f"Error posting Instagram Reel: {e}")


def post_facebook_reel():
    try:
        # Get previously posted properties
        facebook_reels = SocialPost.objects.filter(
            social_media="facebook", content_type="reel"
        )

        last_reel_posted_sound_track = ""
        if facebook_reels:
            last_reel_posted_sound_track = (
                facebook_reels.order_by("-datetime").first().sound_track
                if facebook_reels
                else None
            )
        last_caption_generated = (
            facebook_reels.order_by("-datetime").first().ai_caption
            if facebook_reels
            else None
        )

        # Never-posted first, then least-recently-posted — avoids reposting the
        # same reel over and over once the eligible inventory is exhausted.
        candidates = select_properties_to_post(facebook_reels, PRICE_LIMIT_INSTAGRAM)

        if not candidates:
            logger.warning("No suitable property found to post on Facebook Reels.")
            return

        # Create video, trying candidates until one encodes successfully.
        audio_path = _get_random_mp3_full_path(exclude=last_reel_posted_sound_track)

        property_to_post_facebook_reel = None
        fb_video_meta = {}
        encode_attempts = delisted_skips = 0
        for candidate in candidates:
            if encode_attempts >= MAX_REEL_ATTEMPTS or delisted_skips >= MAX_DELISTED_SKIPS:
                break
            fb_video_meta.clear()
            result = create_property_video(
                candidate.pk,
                output_path="property_video.mp4",
                audio_path=audio_path,
                duration_per_image=3,
                meta=fb_video_meta,
            )
            if result:
                property_to_post_facebook_reel = candidate
                break
            if result is NO_LIVE_IMAGES:
                delisted_skips += 1
                logger.warning(
                    f"Skipping delisted property {candidate.url}; trying next "
                    "(does not count as a video attempt)."
                )
                continue
            encode_attempts += 1
            logger.warning(
                f"Skipping property {candidate.url}: video creation failed, trying next."
            )

        if not property_to_post_facebook_reel:
            logger.error(
                "Could not create a video for any candidate property; nothing posted to Facebook Reels."
            )
            return

        media_dir = os.path.join(settings.MEDIA_ROOT, "generated_videos")
        os.makedirs(media_dir, exist_ok=True)
        target_path = os.path.join(media_dir, "property_video.mp4")
        shutil.move("property_video.mp4", target_path)

        ai_caption, caption, caption_angle = generate_caption_for_post(
            property_to_post_facebook_reel.location,
            property_to_post_facebook_reel.get_public_url,
            property_to_post_facebook_reel.get_price_for_front,
            property_to_post_facebook_reel.building_area,
            property_to_post_facebook_reel.land_area,
            last_caption_generated=last_caption_generated,
            use_ai_caption=USE_AI_CAPTION,
        )

        # Facebook Page Reels use a dedicated 3-phase flow. The old /videos edge
        # posts a normal feed video (not a Reel) and rejects file_url uploads:
        #   1) start  -> open an upload session, get video_id + upload_url
        #   2) upload -> hand Meta the hosted video via the `file_url` header
        #   3) finish -> publish with video_state=PUBLISHED
        page_id = PAGE_ID
        access_token = get_fresh_token()
        reels_url = f"https://graph.facebook.com/v19.0/{page_id}/video_reels"

        # Step 1: start an upload session.
        start_response = requests.post(
            reels_url,
            data={"upload_phase": "start", "access_token": access_token},
        )
        logger.info("Facebook reel start response: " + start_response.text)
        start_data = start_response.json()
        video_id = start_data.get("video_id")
        upload_url = start_data.get("upload_url")
        if not video_id or not upload_url:
            logger.error(
                "Failed to start Facebook Reel upload: " + start_response.text
            )
            return

        # Step 2: upload the video bytes directly. We send the file rather than a
        # file_url because Meta fetches file_url with its `meta-externalagent`
        # crawler, which our (Cloudflare-managed) robots.txt blocks — the hosted
        # path fails with "403 Restricted by robots.txt". A direct upload isn't a
        # crawl, so robots.txt doesn't apply.
        file_size = os.path.getsize(target_path)
        with open(target_path, "rb") as video_file:
            upload_response = requests.post(
                upload_url,
                headers={
                    "Authorization": f"OAuth {access_token}",
                    "offset": "0",
                    "file_size": str(file_size),
                },
                data=video_file,
            )
        logger.info("Facebook reel upload response: " + upload_response.text)
        if not upload_response.json().get("success"):
            logger.error(
                "Failed to upload Facebook Reel video: " + upload_response.text
            )
            return

        # Meta fetches and transcodes the hosted file asynchronously; give it
        # time before asking to publish (mirrors the Instagram flow).
        time.sleep(180)

        # Step 3: publish the reel.
        finish_response = requests.post(
            reels_url,
            data={
                "upload_phase": "finish",
                "video_id": video_id,
                "video_state": "PUBLISHED",
                "description": caption,
                "access_token": access_token,
            },
        )
        logger.info("Facebook reel finish response: " + finish_response.text)

        if finish_response.status_code == 200 and finish_response.json().get("success"):
            logger.info("Successfully posted to Facebook Reels!")

            # Log the post
            SocialPost.objects.create(
                ai_caption=ai_caption,
                caption=caption,
                caption_angle=caption_angle,
                overlay_hook=fb_video_meta.get("overlay_hook", ""),
                media_id=str(video_id or ""),
                property_url=property_to_post_facebook_reel.url,
                social_media="facebook",
                content_type="reel",
                sound_track=audio_path,
            )
        else:
            logger.error("Failed to publish Facebook Reel: " + finish_response.text)

    except Exception as e:
        logger.error(f"Error posting Facebook Reel: {e}")
        notify_social_token_expired(message=f"Error posting Facebook Reel: {e}")


def create_property_video(
    property_id: int,
    output_path: str,
    audio_path: str,
    duration_per_image: int = 3,
    meta: dict = None,
):
    """Build the reel. `meta`, when given, is filled with what was rendered.

    An out-parameter rather than a richer return value because callers already
    branch on this function's return (a path, None, or the NO_LIVE_IMAGES
    sentinel), and the poster needs the burnt-in hook to store beside the post
    so it can later be compared against the numbers it earned.
    """
    if meta is None:
        meta = {}
    W, H = REEL_WIDTH, REEL_HEIGHT
    bold_font = os.path.join(settings.STATIC_ROOT, "fonts", "Montserrat-Bold.ttf")
    light_font = os.path.join(settings.STATIC_ROOT, "fonts", "Montserrat-Light.ttf")
    # The card sets a price in Black, not Bold, and a price is the thing both
    # formats are built around.
    black_font = os.path.join(settings.STATIC_ROOT, "fonts", "Montserrat-Black.ttf")

    llm = ai_client()
    images = PropertyImage.objects.filter(property_id=property_id).order_by("id")[:4]
    property = Property.objects.get(pk=property_id)
    if not images:
        logger.error("No images found for the property.")
        return None

    def _make_slide(local_path):
        """Vertical 9:16 slide, cover-cropped by the same code as the cards.

        This used to fit the photo onto the canvas, which left a 600x450 listing
        photo filling a band across the middle of the frame and two thirds of a
        phone screen black. The type then had to sit in the empty space, and the
        gradients it is designed to sit on had nothing to fade over.

        The old warning that "upscaling to cover is what OOM-killed the VPS" was
        about doing it inside moviepy at 1080x1920. _photo_band does it in PIL,
        at a bounded size, and hands over a JPEG already the size of the frame —
        so what moviepy holds per slide is 540x960 either way, exactly what it
        held before. A photo too small to reach the frame is still letterboxed
        onto the dark canvas below rather than smeared across it.
        """
        _photo_band(local_path, W, H).save(local_path, "JPEG", quality=88)

        img = ImageClip(local_path, duration=duration_per_image).with_position("center")
        if REEL_ENABLE_KEN_BURNS:
            zoom = REEL_KEN_BURNS_ZOOM
            img = img.resized(lambda t: 1 + zoom * (t / duration_per_image))
        bg = ColorClip((W, H), color=REEL_BG_COLOR, duration=duration_per_image)
        return CompositeVideoClip([bg, img], size=(W, H))

    slides = []
    gone = 0
    for img_obj in images:
        img_url = prepare_image_url_for_facebook(img_obj.file.url)
        logger.info(f"Preparing image URL: {img_url}")
        try:
            slides.append(_make_slide(_download_image_to_tempfile(img_url)))
        except Exception as e:
            if is_permanently_gone(e):
                gone += 1
            logger.warning(f" Skipping image {img_url}: {e}")

    if not slides:
        logger.error("No valid images to create video.")
        # Every photo we tried is a hard 404 — the listing has almost certainly
        # been taken down. Confirm against the property's remaining photos
        # (we only sample the first few above) before retiring it, so a
        # property whose later images are still live is merely skipped.
        if gone == len(images) and _remaining_images_gone(property_id, images):
            Property.objects.filter(pk=property_id).update(show_in_front=False)
            logger.warning(
                f"Retired delisted property {property.url}: all images 404. "
                "It no longer blocks the social queue or shows on the site."
            )
            return NO_LIVE_IMAGES
        return None

    # Concatenate with crossfades; fall back to hard cuts if the transition
    # API misbehaves so we never lose the whole video over a transition.
    try:
        faded = [
            s.with_effects([vfx.CrossFadeIn(REEL_CROSSFADE)]) if i else s
            for i, s in enumerate(slides)
        ]
        base = concatenate_videoclips(faded, method="compose", padding=-REEL_CROSSFADE)
    except Exception as exc:
        logger.warning(f"Crossfade concat failed ({exc}); using hard cuts.")
        base = concatenate_videoclips(slides, method="compose")
    base = base.with_effects([vfx.FadeOut(0.5)])

    # Write the base (no-label) video. Lighter preset/bitrate + capped threads
    # keep peak memory low so ffmpeg isn't OOM-killed. On failure, return None
    # so the caller can try another property instead of crashing the whole run.
    try:
        base.write_videofile(
            "property_video_without_label.mp4",
            fps=30,
            codec="libx264",
            audio=audio_path,
            bitrate="2500k",
            preset="ultrafast",
            threads=2,
            ffmpeg_params=[
                "-profile:v",
                "high",
                "-level",
                "4.1",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-g",
                "60",
                "-sc_threshold",
                "0",
            ],
        )
    except Exception as exc:
        logger.error(f"Base video encode failed for property {property_id}: {exc}")
        return None

    # Trim to the visual duration: the audio track is a full song and would
    # otherwise stretch the reel to the song's length with frozen frames.
    target = base.duration
    clip = VideoFileClip("property_video_without_label.mp4").subclipped(0, target)
    dur = clip.duration

    # --- Text overlays -----------------------------------------------------
    # Same design as the listing carousel: left-aligned at the card's margin,
    # the price in Black, the place under it, the size in brass, and gradients
    # rather than hard translucent bands. A reel and a carousel of the same
    # house used to look like two accounts.
    #
    # Sizes are given in the card's own pixels. The card is 1080 wide, a 9:16
    # frame 1080 wide is 1920 tall, and fs() is scaled against 1920 — so fs(126)
    # here is the card's 126px price whatever the reel is rendered at.
    def fs(base):
        return max(14, int(base * H / 1920))

    margin = fs(CARD_MARGIN)
    box_w = W - margin * 2

    def _hex(rgb):
        # PIL takes a tuple, but moviepy hands the colour through several layers
        # to get there and a string survives all of them.
        return "#%02x%02x%02x" % rgb

    def _scrims():
        """The card's two gradients, as one transparent overlay.

        One clip rather than two: it is the same RGBA image either way, and the
        composite has fewer layers to walk on a box with no memory to spare.
        """
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        # Deeper and longer than the card's, and concave rather than convex: a
        # card is read in still light, where a gradient that only darkens near
        # the edge is enough. On video the type sits over whatever the photo
        # happens to be — sunlit concrete, in the render I checked — and the
        # brass size line and the hook both disappeared into it. The exponent
        # below 1 keeps the band dark most of the way across and spends the fade
        # at the very end, where nothing is written.
        for height, strength, at_top in (
            (int(H * 0.40), 240, True), (int(H * 0.46), 238, False)
        ):
            ramp = Image.new("L", (1, height))
            ramp.putdata([
                int(strength * ((1 - i / max(1, height - 1)) if at_top
                                else i / max(1, height - 1)) ** 0.6)
                for i in range(height)
            ])
            band = Image.new("RGBA", (W, height), (*REEL_BG_COLOR, 255))
            band.putalpha(ramp.resize((W, height), Image.BILINEAR))
            canvas.alpha_composite(band, (0, 0 if at_top else H - height))
        return ImageClip(np.array(canvas), transparent=True).with_duration(dur)

    def _line(text, y, box_h, font, font_size, color):
        """One left-aligned block, its own box, pinned to the top of it.

        Every box is generous: moviepy raises when text overflows, and that
        exception is caught below by a fallback that posts the video with no
        overlays at all — no price, no place, not even the watermark.
        """
        return (
            TextClip(
                font=font, text=text, font_size=font_size, color=color,
                method="caption", text_align="left",
                horizontal_align="left", vertical_align="top",
                size=(box_w, box_h),
            )
            .with_duration(dur)
            .with_position((margin, y))
        )

    def _price_stack(top):
        """Price, place, size — the hero card's block, in the same order."""
        # Tiered rather than shrunk to fit: see _line on what an overflow costs.
        price_size = fs(126) if len(price) <= 10 else (
            fs(96) if len(price) <= 13 else fs(76)
        )
        clips = [_line(price, top, fs(150), black_font, price_size, _hex(FG))]
        if place:
            clips.append(
                _line(place, top + fs(160), fs(120), light_font, fs(46), _hex(FG))
            )
        if details:
            clips.append(
                _line(details, top + fs(292), fs(56), light_font, fs(32),
                      _hex(ACCENT))
            )
        return clips

    def _hook_stack(top):
        """The AI phrase, with the wordmark under it — the later slides' bar."""
        return [
            _line(video_top_text, top, fs(140), light_font, fs(52), _hex(FG)),
            _line(REEL_BRAND_TEXT, top + fs(150), fs(60), bold_font, fs(32),
                  _hex(FG)),
        ]

    # Top: short AI hook, sanitised to ASCII (the model sometimes injects CJK).
    try:
        raw_top = llm.generate_text(
            prompt="Generate a short, punchy 2-4 word overlay phrase in ENGLISH ONLY for a"
            " Japan property reel (e.g. 'Your Quiet Escape'). No quotes, no emojis,"
            " no non-English characters, title case."
        )
    except Exception as exc:
        logger.warning(f"No model gave us overlay text, using the default: {exc}")
        raw_top = None
    top_clean = re.sub(r"[^A-Za-z0-9 &!'-]", "", raw_top or "").strip() if raw_top else ""
    video_top_text = top_clean[:24].strip() or "Link in Bio"

    price = str(property.get_price_for_front or "")
    # The same place name and the same size line the card carries, from the same
    # code — the two formats naming one house differently was the tell that they
    # were built separately. Truncated on a word, not shrunk.
    place = _card_location(property, max_chars=REEL_HOOK_PLACE_MAX_CHARS)
    details = _details_line(
        _clean_area(property.building_area), _clean_area(property.land_area)
    )

    # What the reel is remembered by, and the thing to compare against the
    # insights later.
    meta["overlay_hook"] = video_top_text
    meta["hook_price_first"] = REEL_HOOK_PRICE_FIRST

    overlays = [clip, _scrims()]
    if REEL_HOOK_PRICE_FIRST:
        # Frame one, top of the screen: the price, then where it is, then how
        # big. The AI phrase goes to the bottom — it is atmosphere, not a hook,
        # and it was occupying the only line anyone reads before deciding to
        # scroll.
        # 0.74 rather than lower: Instagram's own caption and buttons cover the
        # bottom of a reel, and the wordmark was sitting under them.
        overlays += _price_stack(margin) + _hook_stack(int(H * 0.74))
    else:
        overlays += _hook_stack(margin) + _price_stack(int(H * 0.58))

    # Composite overlays onto the base video. On any failure, fall back to the
    # already-written no-label video so the bot still posts something.
    try:
        final_video = CompositeVideoClip(overlays, size=(W, H))
        final_video.write_videofile(
            output_path,
            fps=30,
            codec="libx264",
            audio_codec="aac",
            preset="ultrafast",
            threads=2,
            ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
        )
        logger.info(f"Video created with overlays: {output_path}")
        return output_path
    except Exception as exc:
        logger.warning(
            f"Text-overlay composite failed ({exc}); falling back to no-label video."
        )
        shutil.copy("property_video_without_label.mp4", output_path)
        logger.info(f"Video created (no overlays): {output_path}")
        return output_path
