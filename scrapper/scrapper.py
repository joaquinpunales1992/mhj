from __future__ import annotations

import re
import time

import chardet
import requests
from deep_translator import GoogleTranslator

from inventory.models import Property, PropertyImage
from inventory.utils import parse_transit
from scrapper.constants import MAX_PRICE_TO_PULL


REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4 Safari/605.1.15"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.9",
}

MAX_TRANSLATE_CHARS = 5000


# Signatures for common anti-bot challenge pages (AWS WAF, Imperva/Reese,
# Cloudflare, Akamai). These return 200 (or 202) with a tiny JS-challenge body.
_BOT_CHALLENGE_SIGNATURES = (
    "x-amzn-waf-action",          # AWS WAF (LIFULL)
    "reeseSkipExpirationCheck",   # Imperva/Reese (At Home)
    "onProtectionInitialized",    # Imperva
    "Just a moment...",           # Cloudflare
    "challenge-platform",         # Cloudflare
    "認証中",                      # 'authenticating' interstitial
)


def _looks_like_bot_challenge(response: requests.Response) -> bool:
    if response.headers.get("x-amzn-waf-action"):
        return True
    if response.status_code == 202:
        return True
    body = response.text
    if len(body) < 20000:
        for marker in _BOT_CHALLENGE_SIGNATURES:
            if marker in body:
                return True
    return False


def fetch(url: str, *, headers: dict | None = None, timeout: int = 20) -> requests.Response | None:
    """GET a URL with sensible defaults. Returns None on non-200, transport error, or bot challenge."""
    try:
        response = requests.get(url, headers=headers or REQUEST_HEADERS, timeout=timeout)
    except requests.RequestException as exc:
        print(f"Request error for {url}: {exc}")
        return None

    if response.status_code != 200:
        print(f"Failed to retrieve {url}: status {response.status_code}")
        return None

    if _looks_like_bot_challenge(response):
        print(f"Bot challenge / WAF block detected for {url}")
        return None

    encoding = chardet.detect(response.content)["encoding"]
    if encoding:
        response.encoding = encoding
    return response


def parse_jpy_price(text: str) -> int | None:
    """Parse a Japanese yen price string to an int (yen).

    Handles '1980万円', '1,980万円', '2億9800万円', '1億円', '580 万円', and
    plain-yen forms like '5800000円'. Must be called on the raw Japanese
    string before translation — translators drop the 万/億 markers.
    """
    if not text:
        return None
    clean = text.replace(",", "").replace("，", "")
    m = re.search(r"(?:(\d+)\s*億)?\s*(?:(\d+)\s*万)?\s*円", clean)
    if not m:
        return None
    oku_str, man_str = m.group(1), m.group(2)
    if not oku_str and not man_str:
        plain = re.search(r"(\d+)\s*円", clean)
        return int(plain.group(1)) if plain else None
    oku = int(oku_str) if oku_str else 0
    man = int(man_str) if man_str else 0
    return oku * 100_000_000 + man * 10_000


def parse_jp_date(text: str):
    """Parse a Japanese listing date to a `date`, or None.

    Handles '2026年8月19日', '2026/8/19' and '2026-08-19'. Like parse_jpy_price
    this must run on the raw Japanese: the translator rewrites the string into
    prose ("August 19, 2026") and sometimes drops the day entirely.
    """
    if not text:
        return None
    import datetime

    match = re.search(r"(\d{4})\s*[年/\-.]\s*(\d{1,2})\s*[月/\-.]\s*(\d{1,2})", str(text))
    if not match:
        return None
    year, month, day = (int(g) for g in match.groups())
    try:
        return datetime.date(year, month, day)
    except ValueError:
        # A listing dated 31 February is a scrape artefact, not a date.
        return None


# Google's error pages, as they read once the translator has stripped the HTML
# out of them. The endpoint answers 500 with a page rather than an error status
# the client library recognises, so what comes back looks like a successful
# translation and gets saved as one.
#
# Matching on text is unpleasant, but the alternative is not available: by the
# time safe_translate sees this, deep-translator has thrown the status code
# away. Both apostrophes are here because the page uses a typographic one and
# it is not worth betting that it always will.
TRANSLATION_ERROR_MARKERS = (
    "That’s all we know",
    "That's all we know",
    "That’s an error",
    "That's an error",
    # MyMemory does the same thing Google does — hands back its refusal as if
    # it were the translation, in capitals.
    "MYMEMORY WARNING",
    "YOU USED ALL AVAILABLE FREE TRANSLATIONS",
    "PLEASE SELECT TWO DISTINCT LANGUAGES",
)


def looks_like_an_error_page(text: str) -> bool:
    """True when a 'translation' is actually Google's error page.

    This is how listings ended up titled "Error 500 (Server Error)!!1500.
    That's an error..." on the site — and, since get_title_for_front cuts at 20
    characters, how a row of cards ended up reading "Error 500 (Server Er...".
    """
    return any(marker in text for marker in TRANSLATION_ERROR_MARKERS)


def safe_translate(value: str | None, translator: GoogleTranslator | None = None) -> str:
    """Translate to English, or give back the original untouched.

    Never returns something that was not a translation. A failure here is
    written to the database and displayed for as long as the row lives, so the
    Japanese source text is the right thing to keep: it is honest about what we
    have, and a later run can still translate it. Google's apology cannot be
    turned back into a house.
    """
    if not value:
        return ""
    translator = translator or GoogleTranslator(source="auto", target="en")
    original = value[:MAX_TRANSLATE_CHARS]
    try:
        translated = translator.translate(original) or ""
    except Exception as exc:
        print(f"Translation error: {exc}")
        return original

    if looks_like_an_error_page(translated):
        # Not raised, so the try above cannot catch it: the request "succeeded"
        # and returned a page about having failed.
        print(f"Translator returned an error page for {original[:40]!r}; trying MyMemory.")
        return (
            translate_with_mymemory(original)
            or translate_with_model(original)
            or original
        )
    return translated


# MyMemory refuses anything longer in a single request.
MYMEMORY_MAX_CHARS = 500


def translate_with_mymemory(value: str) -> str:
    """A second translation service, before reaching for a model.

    The free Google endpoint blocks the address a burst came from. A model can
    translate its way around that, but it is the wrong tool and the scarcest
    one: the Gemini free tier allows twenty requests per model per day, which
    does not go far against a hundred fields.

    MyMemory is a translation service, needs no key, and handles these strings
    as well as Google did — "接道と段差有、建築基準法第22条" comes back as
    "Access roads and steps available, Building Standards Act Article 22".

    Japanese in, English out, stated rather than auto-detected: this is only
    ever reached for fields scraped from Japanese listing sites, and MyMemory
    wants a concrete pair.

    Returns "" when it fails or refuses, which sends the caller on to the model.
    """
    if not value:
        return ""
    try:
        from deep_translator import MyMemoryTranslator

        answer = MyMemoryTranslator(source="ja-JP", target="en-GB").translate(
            value[:MYMEMORY_MAX_CHARS]
        )
    except Exception as exc:
        print(f"MyMemory failed: {exc}")
        return ""

    answer = (answer or "").strip()
    # It announces a spent quota in the response body, the same way Google
    # announces a 500 — so the same test has to be applied to the answer.
    if not answer or looks_like_an_error_page(answer):
        print("MyMemory refused; asking the model.")
        return ""
    return answer


# Enough of the field to be sure, and short enough that a stray instruction
# inside a listing cannot turn into a paragraph.
MODEL_TRANSLATION_PROMPT = (
    "Translate this Japanese real-estate listing text into English.\n"
    "Reply with the translation and nothing else — no quotes, no notes, no "
    "explanation. Keep numbers, areas and measurements exactly as they are. "
    "If it is already English, reply with it unchanged.\n\n"
    "{text}"
)


def translate_with_model(value: str) -> str:
    """Translate through the LLM chain, for when the free endpoint will not.

    The scraped endpoint answers a burst of requests by blocking the address it
    came from, which is not a cooldown you can wait out on a server with one IP.
    The bot already holds keys for Gemini and Cerebras; an authenticated API is
    not subject to that block, and translating a listing title is well inside
    what it does.

    Second, not first: the free endpoint is free, and this runs only for fields
    that would otherwise be stored as an error page.

    Returns "" when there is no model configured or it fails, which leaves the
    caller holding the Japanese — the same outcome as before this existed.
    """
    try:
        from ai.providers import ai_client

        answer = ai_client().generate_text(MODEL_TRANSLATION_PROMPT.format(text=value))
    except Exception as exc:
        print(f"Model translation failed: {exc}")
        return ""

    answer = (answer or "").strip()
    # A model that has decided to explain itself has not translated anything.
    # Listing fields are short; a paragraph back from a phrase is a refusal or
    # a preamble, and the Japanese is better than either.
    if not answer or len(answer) > max(400, len(value) * 6):
        return ""
    return answer


def run_source(
    source: str,
    region: str,
    page_from: int = 1,
    page_to: int = 50,
    dry_run: bool = False,
) -> None:
    """Drive a scrape: walk listing pages, parse each detail URL, persist."""
    from scrapper.sources import SOURCES

    if source not in SOURCES:
        raise ValueError(f"Unknown source {source!r}. Available: {sorted(SOURCES)}")
    module = SOURCES[source]

    for page in range(page_from, page_to + 1):
        try:
            urls = module.iter_listing_urls(region=region, page=page)
        except Exception as exc:
            print(f"[{source}] Error listing page {page}: {exc}")
            continue

        if not urls:
            print(f"[{source}] No listings on page {page}, stopping.")
            return

        print(f"[{source}] page {page}: {len(urls)} listings")
        for url in urls:
            try:
                data = module.parse_listing(url=url)
            except Exception as exc:
                print(f"[{source}] Error parsing {url}: {exc}")
                continue
            if not data:
                continue
            if dry_run:
                print(
                    f"[dry-run] {data.get('property_title','')!r} "
                    f"price={data.get('property_price','')!r} "
                    f"yen={data.get('property_price_yen')!r} "
                    f"images={len(data.get('image_urls', []))}"
                )
            else:
                persist_property(property_data=data)
            time.sleep(1)


def persist_property(property_data: dict) -> None:
    """Save property data to the database.

    Expects `property_price_yen` (int) in property_data — the source module
    must extract it from the raw Japanese string before translation, since
    translators drop the 万/億 markers needed to recover the magnitude.
    Stored on Property.price in 万 units to match the existing schema.
    """
    try:
        property_price_yen = property_data.get("property_price_yen")
        if not property_price_yen:
            print(f"No parseable price for {property_data.get('property_url')!r}")
            return

        property_price_man = property_price_yen // 10_000
        if property_price_man > MAX_PRICE_TO_PULL:
            print(
                f"Property {property_data.get('property_title')!r} "
                f"exceeds price limit ({property_price_man}万)."
            )
            return

        property_obj, created = Property.objects.get_or_create(
            url=property_data["property_url"]
        )
        if not created:
            # Already stored. Earlier runs (before image extraction was fixed)
            # could persist a property with no images; if this one is still
            # imageless, backfill the photos we just scraped rather than
            # skipping it. Other fields are left untouched so any manual
            # curation survives.
            image_urls = property_data.get("image_urls", [])
            if image_urls and not property_obj.property_has_any_image():
                labels = property_data.get("image_labels") or {}
                for image_url in image_urls:
                    PropertyImage.objects.create(
                        property=property_obj, file=image_url,
                        label=labels.get(image_url, ""),
                    )
                print(
                    f"Backfilled {len(image_urls)} images: {property_obj.title!r}"
                )
            else:
                print(f"Property {property_data['property_url']} already exists.")
            return

        property_obj.url = property_data["property_url"]
        property_obj.title = property_data.get("property_title", "")
        property_obj.traffic = property_data.get("traffic", "")
        property_obj.location = property_data.get("location", "")
        property_obj.description = property_data.get("remarks", "")
        property_obj.construction_date = property_data.get("building_age", "")
        property_obj.building_structure = property_data.get("building_structure", "")
        property_obj.road_condition = property_data.get("road_condition", "")
        property_obj.setback = property_data.get("setback", "")
        property_obj.city_planning = property_data.get("city_planning", "")
        property_obj.zoning = property_data.get("zoning", "")
        property_obj.land_category = property_data.get("land_category", "")
        property_obj.building_coverage_ratio = property_data.get("building_coverage_ratio", "")
        property_obj.floor_area_ratio = property_data.get("floor_area_ratio", "")
        property_obj.current_status = property_data.get("current_status", "")
        property_obj.handover = property_data.get("handover", "")
        property_obj.equipment = property_data.get("equipment", "")
        property_obj.transaction_type = property_data.get("transaction_type", "")
        property_obj.price = property_price_man
        property_obj.floor_plan = property_data.get("floor_plan", "")
        property_obj.building_area = property_data.get("building_area", "")
        property_obj.land_area = property_data.get("land_area", "")
        property_obj.parking = property_data.get("parking", "")
        property_obj.construction = property_data.get("building_age", "")
        property_obj.land_rights = property_data.get("land_rights", "")
        property_obj.renovation = property_data.get("renovation", "")
        property_obj.estimated_utility_cost = property_data.get("estimated_utility_cost", "")
        property_obj.insulation_performance = property_data.get("insulation_performance", "")
        property_obj.energy_performance = property_data.get("energy_performance", "")
        property_obj.listed_on = property_data.get("listed_on")

        # Derive the station fields here rather than in a later pass, so a newly
        # scraped listing is filterable the moment it lands.
        transit = parse_transit(property_obj.traffic)
        property_obj.nearest_station = transit["station"]
        property_obj.station_walk_minutes = transit["walk_minutes"]
        property_obj.station_distance_km = transit["distance_km"]
        property_obj.needs_bus = transit["needs_bus"]

        property_obj.save()

        for image_url in property_data.get("image_urls", []):
            PropertyImage.objects.create(
                property=property_obj, file=image_url,
                label=(property_data.get("image_labels") or {}).get(image_url, ""),
            )

        print(f"Saved: {property_obj.title!r}")

    except Exception as exc:
        print(f"Error saving property: {exc}")
