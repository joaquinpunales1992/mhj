from __future__ import annotations

import re
import time

import chardet
import requests
from deep_translator import GoogleTranslator

from inventory.models import Property, PropertyImage
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


def safe_translate(value: str | None, translator: GoogleTranslator | None = None) -> str:
    if not value:
        return ""
    translator = translator or GoogleTranslator(source="auto", target="en")
    try:
        return translator.translate(value[:MAX_TRANSLATE_CHARS]) or ""
    except Exception as exc:
        print(f"Translation error: {exc}")
        return value[:MAX_TRANSLATE_CHARS]


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
                    f"images={len(data.get('image_urls', []))}"
                )
            else:
                persist_property(property_data=data)
            time.sleep(1)


def persist_property(property_data: dict) -> None:
    """Save property data to the database."""
    try:
        price_str = property_data.get("property_price", "") or ""
        price_match = re.search(r"([\d,]+)", price_str)
        if not price_match:
            print(f"Could not parse price: {price_str!r}")
            return

        price_value = int(price_match.group(1).replace(",", ""))
        if "万" in price_str or "man" in price_str.lower():
            property_price = price_value * 10000
        else:
            property_price = price_value

        if property_price > MAX_PRICE_TO_PULL * 10000:
            print(
                f"Property {property_data.get('property_title')!r} "
                f"exceeds price limit ({property_price})."
            )
            return

        property_obj, created = Property.objects.get_or_create(
            url=property_data["property_url"]
        )
        if not created:
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
        property_obj.price = property_price
        property_obj.floor_plan = property_data.get("floor_plan", "")
        property_obj.building_area = property_data.get("building_area", "")
        property_obj.land_area = property_data.get("land_area", "")
        property_obj.parking = property_data.get("parking", "")
        property_obj.construction = property_data.get("building_age", "")
        property_obj.land_rights = property_data.get("land_rights", "")
        property_obj.save()

        for image_url in property_data.get("image_urls", []):
            PropertyImage.objects.create(property=property_obj, file=image_url)

        print(f"Saved: {property_obj.title!r}")

    except Exception as exc:
        print(f"Error saving property: {exc}")
