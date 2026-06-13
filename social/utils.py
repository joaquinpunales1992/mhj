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
from ai.cerebras import CerebrasAI
from social.models import SocialPost, SocialComment
from inventory.models import Property, PropertyImage
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
from PIL import Image
from membership.utils import notify_social_token_expired
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

# When a reel's video fails to encode (e.g. ffmpeg OOM-killed on the VPS), the
# poster moves on to the next candidate property rather than aborting the run.
# Cap the attempts so a bad batch doesn't churn through many heavy encodes.
MAX_REEL_ATTEMPTS = 3


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
    """Normalise a scraped area string like '198.73m 2 （60.11坪）（登記）'.

    -> '198.73 m² (60.11 tsubo)'. Drops registration notes like （登記）.
    """
    if not area:
        return ""
    area = str(area)
    area = re.sub(r"（\s*登記\s*）|\(\s*登記\s*\)", "", area)  # drop registration note
    area = re.sub(r"m\s*2|㎡", "m²", area)                      # 'm 2' / '㎡' -> 'm²'
    # （60.11坪） -> (60.11 tsubo)
    area = re.sub(r"[（(]\s*([\d.]+)\s*坪\s*[）)]", r"(\1 tsubo)", area)
    return re.sub(r"\s+", " ", area).strip()


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

    Eligibility: must have at least one image and a real price within
    (0, price_limit]. Featured is NOT required — it's only a tiebreaker.

    Ordering: never-posted properties come first so fresh inventory gets a
    turn before anything is reposted; within each group we sort cheapest
    first, then prefer featured, and within the already-posted group we
    surface the least-recently-posted to keep the rotation fair (so we don't
    spam the single cheapest property once inventory is exhausted).

    `posts_queryset` is the SocialPost rows for the relevant channel; matching
    is by property_url == Property.url (same value written when a post is made).
    """
    rows = posts_queryset.values("property_url").annotate(last=Max("datetime"))
    last_posted = {r["property_url"]: r["last"] for r in rows}

    candidates = list(
        Property.objects.filter(
            images__isnull=False, price__gt=0, price__lte=price_limit
        ).distinct()
    )
    # Sort key, in priority order:
    #   1. already-posted?    never-posted ahead of posted
    #   2. last-posted-time   oldest repost first (only separates the posted group)
    #   3. price              cheapest first
    #   4. not featured       featured wins ties (False < True)
    candidates.sort(
        key=lambda p: (
            last_posted.get(p.url) is not None,
            last_posted.get(p.url) or 0,
            p.price or 0,
            not p.featured,
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

    def _details_block():
        lines = [f"💰 {property_price}", f"📍 {location}"]
        if building_area:
            lines.append(f"🏡 Building: {building_area}")
        if land_area:
            lines.append(f"🌳 Land: {land_area}")
        lines.append(f"\n🔗 www.akiyainjapan.com{property_url}")
        return "\n".join(lines) + f"\n\n{hashtags}"

    ai_caption = ""
    if use_ai_caption:
        try:
            cerebras_ai_client = CerebrasAI()

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

            ai_caption = cerebras_ai_client.generate_text(
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
                    "- Do NOT include hashtags, the price, or the address (added separately).\n"
                    f"- Do NOT repeat this previous caption: {last_caption_generated}\n"
                    "Output ONLY the caption text."
                )
            )

            ai_caption = _sanity_check_ai_caption(ai_caption)
            caption = f"{ai_caption}\n\n{_details_block()}"
            logger.info(f"Caption generated via AI: {caption}")
        except Exception as e:
            caption = _details_block()
            logger.error(f"AI caption generation failed: {e}")
        return ai_caption, caption
    else:
        logger.info("AI caption generation is disabled, using default caption format.")
        return ai_caption, _details_block()


def post_to_instagram(
    property: Property, last_caption_generated: str, use_ai_caption: bool
):
    property_image_urls = [image.file.url for image in property.images.all()][:5]

    ai_caption, caption = generate_caption_for_post(
        property_location=property.location,
        property_url=property.get_public_url,
        property_price=property.get_price_for_front,
        property_building_area=property.building_area,
        property_land_area=property.land_area,
        last_caption_generated=last_caption_generated,
        use_ai_caption=use_ai_caption,
    )

    media_ids = []

    # Upload each image as a carousel item
    for raw_url in property_image_urls:
        try:
            image_url = prepare_image_url_for_facebook(raw_url)
        except Exception as e:
            logger.error(f"Error preparing image URL: {e}")
            continue

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
    property_image_urls = [image.file.url for image in property.images.all()][:5]

    ai_caption, caption = generate_caption_for_post(
        property_location=property.location,
        property_url=property.get_public_url,
        property_price=property.get_price_for_front,
        property_building_area=property.building_area,
        property_land_area=property.land_area,
        last_caption_generated=last_caption_generated,
        use_ai_caption=use_ai_caption,
    )

    # Upload each image (unpublished)
    media_fbids = []
    for raw_url in property_image_urls:
        try:
            image_url = prepare_image_url_for_facebook(raw_url)
        except Exception as e:
            logger.error(f"Error preparing image URL: {e}")
            continue

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
        for candidate in candidates[:MAX_REEL_ATTEMPTS]:
            if create_property_video(
                candidate.pk,
                output_path="property_video.mp4",
                audio_path=audio_path,
                duration_per_image=3,
            ):
                property_to_post_instagram_reel = candidate
                break
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

        ai_caption, caption = generate_caption_for_post(
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
            "share_to_feed": False,
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

                # Log the post
                SocialPost.objects.create(
                    ai_caption=ai_caption,
                    caption=caption,
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
        for candidate in candidates[:MAX_REEL_ATTEMPTS]:
            if create_property_video(
                candidate.pk,
                output_path="property_video.mp4",
                audio_path=audio_path,
                duration_per_image=3,
            ):
                property_to_post_facebook_reel = candidate
                break
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

        video_url = "https://akiyainjapan.com/media/generated_videos/property_video.mp4"

        ai_caption, caption = generate_caption_for_post(
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

        # Step 2: tell Meta where to fetch the video from (hosted on our site).
        upload_response = requests.post(
            upload_url,
            headers={
                "Authorization": f"OAuth {access_token}",
                "file_url": video_url,
            },
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
    property_id: int, output_path: str, audio_path: str, duration_per_image: int = 3
):
    W, H = REEL_WIDTH, REEL_HEIGHT
    bold_font = os.path.join(settings.STATIC_ROOT, "fonts", "Montserrat-Bold.ttf")
    light_font = os.path.join(settings.STATIC_ROOT, "fonts", "Montserrat-Light.ttf")

    cerebras_ai_client = CerebrasAI()
    images = PropertyImage.objects.filter(property_id=property_id).order_by("id")[:4]
    property = Property.objects.get(pk=property_id)
    if not images:
        logger.error("No images found for the property.")
        return None

    def _make_slide(local_path):
        """Vertical 9:16 slide: photo fit onto a dark canvas (downscale only).

        We pre-shrink with PIL so a large source image is never fully decoded
        into a giant array — upscaling to 'cover' is what OOM-killed the VPS.
        """
        # Downscale to fit within the canvas, in place (Pillow only shrinks).
        with Image.open(local_path) as im:
            im = im.convert("RGB")
            im.thumbnail((W, H), Image.LANCZOS)
            im.save(local_path, "JPEG", quality=88)

        img = ImageClip(local_path, duration=duration_per_image).with_position("center")
        if REEL_ENABLE_KEN_BURNS:
            zoom = REEL_KEN_BURNS_ZOOM
            img = img.resized(lambda t: 1 + zoom * (t / duration_per_image))
        bg = ColorClip((W, H), color=REEL_BG_COLOR, duration=duration_per_image)
        return CompositeVideoClip([bg, img], size=(W, H))

    slides = []
    for img_obj in images:
        img_url = prepare_image_url_for_facebook(img_obj.file.url)
        logger.info(f"Preparing image URL: {img_url}")
        try:
            slides.append(_make_slide(_download_image_to_tempfile(img_url)))
        except Exception as e:
            logger.warning(f" Skipping image {img_url}: {e}")

    if not slides:
        logger.error("No valid images to create video.")
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
    # Font sizes are tuned for a 1920px-tall canvas and scaled to the actual
    # height so the layout looks right at 540, 720 or 1080.
    def fs(base):
        return max(14, int(base * H / 1920))

    def _band(y_frac, h_frac, opacity=0.45):
        return (
            ColorClip((W, int(H * h_frac)), color=(0, 0, 0), duration=dur)
            .with_opacity(opacity)
            .with_position((0, int(H * y_frac)))
        )

    def _centered_text(text, y_frac, h_frac, font, font_size):
        return (
            TextClip(
                font=font,
                text=text,
                font_size=font_size,
                color="white",
                stroke_color="black",
                stroke_width=3,
                method="caption",
                text_align="center",
                size=(W - 140, int(H * h_frac)),
            )
            .with_duration(dur)
            .with_position(("center", int(H * y_frac)))
        )

    # Top: short AI hook, sanitised to ASCII (the model sometimes injects CJK).
    try:
        raw_top = cerebras_ai_client.generate_text(
            prompt="Generate a short, punchy 2-4 word overlay phrase in ENGLISH ONLY for a"
            " Japan property reel (e.g. 'Your Quiet Escape'). No quotes, no emojis,"
            " no non-English characters, title case."
        )
    except Exception as exc:
        logger.warning(f"Cerebras failed for video overlay text, using default: {exc}")
        raw_top = None
    top_clean = re.sub(r"[^A-Za-z0-9 &!'-]", "", raw_top or "").strip() if raw_top else ""
    video_top_text = top_clean[:24].strip() or "Link in Bio"

    # Bottom info: price + cleaned location.
    price = property.get_price_for_front
    loc = _clean_location(property.get_location_for_front())

    overlays = [
        clip,
        _band(0.05, 0.11),
        _centered_text(video_top_text, 0.05, 0.11, light_font, fs(60)),
        _band(0.66, 0.17),
        _centered_text(f"{price}\n{loc}", 0.66, 0.17, bold_font, fs(54)),
        # Persistent brand watermark.
        TextClip(
            font=bold_font,
            text=REEL_BRAND_TEXT,
            font_size=fs(36),
            color="white",
            stroke_color="black",
            stroke_width=2,
        )
        .with_duration(dur)
        .with_position(("center", int(H * 0.91))),
    ]

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
