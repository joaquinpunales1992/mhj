import re

import requests


# Deliberately no model imports here: inventory.models imports this module.

_LIVENESS_HEADERS = {"User-Agent": "Mozilla/5.0"}


def is_permanently_gone(exc) -> bool:
    """True only for a definitive "this image no longer exists" response.

    Deliberately narrow. Timeouts, connection errors, 403s and 5xx are all
    transient or blocking, and treating them as "gone" would retire live
    properties on a network blip. Only 404/410 count.
    """
    response = getattr(exc, "response", None)
    return response is not None and response.status_code in (404, 410)


def all_images_gone(image_urls) -> bool:
    """True when every given photo URL is a hard 404/410 — a delisted listing.

    Returns False as soon as one photo loads, so the common case (a live
    property) costs a single request.

    Use GET, not HEAD: some sources — homes.jp's image.php in particular —
    answer HEAD with a 404 for images they serve perfectly well on GET, which
    would fake a site-wide outage.

    Vacuously True for an empty sequence. Callers decide whether they have
    enough evidence to retire a property; "no photos at all" is not proof the
    source listing is gone.
    """
    for url in image_urls:
        try:
            with requests.get(
                url, headers=_LIVENESS_HEADERS, stream=True, timeout=30
            ) as response:
                response.raise_for_status()
            return False  # still serving a photo, so the listing is alive
        except Exception as exc:
            if not is_permanently_gone(exc):
                return False  # transient failure — never retire on a maybe
    return True


def parse_area_to_m2(text):
    """Pull the leading square-meterage out of a free-text area field.

    Scraped values look like '103.24㎡ (crystal)' or '170㎡ (public book)';
    we want the first number. Returns a float, or None if unparseable.
    """
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", text.replace(",", ""))
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value if value > 0 else None


def convert_price_string(price):
    try:
        # Convert to int and multiply by 10,000
        return int(price) * 10000
    except (ValueError, AttributeError):
        raise ValueError(f"Invalid price: {price}")


# "US$", not "$". The site quotes Japanese property to a mostly non-US audience,
# where a bare "$" is ambiguous — Canadian, Australian and Singapore dollars all
# use it, and the same page also shows yen. Pricing and the consultation already
# said "US$"; the listings said "$".
CURRENCY_PREFIX = "US$"
YEN_TO_USD = 0.007


def format_usd(amount):
    """`US$12,345` — whole dollars, thousands separated.

    Formatted explicitly rather than via locale.currency(), which returned the
    symbol for whatever locale happened to be installed and raises outright under
    the C locale. That made the site's prices depend on the server's locale being
    configured, which is not a dependency price display should have.
    """
    try:
        value = round(float(amount))
    except (TypeError, ValueError):
        return ""
    return f"{CURRENCY_PREFIX}{value:,}"


def convert_yen_to_usd(price):
    """A yen amount rendered as US dollars, for display."""
    try:
        return format_usd(float(price) * YEN_TO_USD)
    except (TypeError, ValueError):
        return ""


def infer_location(location):
    if "tokyo" in location.lower():
        return "Tokyo"
    elif "osaka" in location.lower():
        return "Osaka"
    elif "shizuoka" in location.lower():
        return "Shizuoka"
    elif "kanagawa" in location.lower():
        return "Kanagawa"
    elif "aichi" in location.lower():
        return "Aichi"
    elif "hyogo" in location.lower():
        return "Hyogo"
    elif "chiba" in location.lower():
        return "Chiba"
    elif "saitama" in location.lower():
        return "Saitama"
    elif "fukuoka" in location.lower():
        return "Fukuoka"
    elif "hiroshima" in location.lower():
        return "Hiroshima"
    elif "kyoto" in location.lower():
        return "Kyoto"
    elif "nagoya" in location.lower():
        return "Nagoya"
    elif "kagawa" in location.lower():
        return "Kagawa"
    elif "okayama" in location.lower():
        return "Okayama"
    elif "miyagi" in location.lower():
        return "Miyagi"
    elif "niigata" in location.lower():
        return "Niigata"
    elif "ishikawa" in location.lower():
        return "Ishikawa"
    elif "nagano" in location.lower():
        return "Nagano"
    elif "gunma" in location.lower():
        return "Gunma"
    elif "tochigi" in location.lower():
        return "Tochigi"
    elif "ibaraki" in location.lower():
        return "Ibaraki"
    elif "yamagata" in location.lower():
        return "Yamagata"
    elif "fukushima" in location.lower():
        return "Fukushima"
    elif "shimane" in location.lower():
        return "Shimane"
    elif "tottori" in location.lower():
        return "Tottori"
    elif "nagasaki" in location.lower():
        return "Nagasaki"
    elif "kumamoto" in location.lower():
        return "Kumamoto"
    elif "ehime" in location.lower():
        return "Ehime"
    elif "kagoshima" in location.lower():
        return "Kagoshima"
    elif "okinawa" in location.lower():
        return "Okinawa"
    elif "aomori" in location.lower():
        return "Aomori"
    elif "akita" in location.lower():
        return "Akita"
    elif "yamaguchi" in location.lower():
        return "Yamaguchi"
    elif "toyama" in location.lower():
        return "Toyama"
    elif "gifu" in location.lower():
        return "Gifu"
    elif "shizuoka" in location.lower():
        return "Shizuoka"
    elif "wakayama" in location.lower():
        return "Wakayama"
    elif "nara" in location.lower():
        return "Nara"
    elif "miyazaki" in location.lower():
        return "Miyazaki"
    elif "kagawa" in location.lower():
        return "Kagawa"
    elif "yamaguchi" in location.lower():
        return "Yamaguchi"
    elif "tokushima" in location.lower():
        return "Tokushima"
    elif "oita" in location.lower():
        return "Oita"
    elif "fukui" in location.lower():
        return "Fukui"
    elif "shiga" in location.lower():
        return "Shiga"
    elif "hokkaido" in location.lower():
        return "Hokkaido"
    elif "kochi" in location.lower():
        return "Kochi"
    elif "saga" in location.lower():
        return "Saga"
    elif "mie prefecture" in location.lower():
        return "Mie"
    else:
        return location


def city_key(location):
    """Collapse a scraped address to a 'City, Prefecture' key for geocoding.

    Scraped addresses run from bare "Fukuroi City, Shizuoka Prefecture" to
    "1418-20 Nishiyamacho, Chuo-ku, Hamamatsu City, Shizuoka Prefecture". The
    last two comma-separated segments are reliably city + prefecture across
    both shapes, which is the granularity the map plots at.

    Returns "" when there's nothing usable, so callers can skip the row.
    """
    if not location:
        return ""

    # SUUMO injects UI junk like "[ ■ Surrounding environment]". Strip the
    # bracketed span itself rather than everything after it — the junk is
    # sometimes mid-string, and truncating there would throw away the city and
    # prefecture that follow. An unclosed bracket has no reliable end, so in
    # that case fall back to dropping the tail.
    cleaned = re.sub(r"\[[^\]]*\]", " ", location)
    cleaned = cleaned.split("[", 1)[0]
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return _strip_house_number(parts[0])
    city, prefecture = parts[-2], parts[-1]
    return f"{_strip_house_number(city)}, {prefecture}"


def _strip_house_number(segment):
    """Drop a leading lot/house number from an address segment.

    Two-segment addresses like "1-257-4 Oita City, Oita Prefecture" would
    otherwise carry the lot number into the geocoding key, splitting one city
    into many near-duplicate keys and giving the geocoder a string it is more
    likely to miss.
    """
    stripped = re.sub(r"^[\d\-‐-―]+\s+", "", segment).strip()
    # Guard against a segment that is *only* a number: keep the original rather
    # than returning an empty string.
    return stripped or segment


def scatter_offset(seed, spread=0.045):
    """Deterministic (dlat, dlng) jitter so properties sharing a city centroid
    don't stack into one unclickable pin.

    Deterministic on purpose: a pin must not hop to a new spot on every page
    load. Spread is roughly a few km — small enough to stay inside the right
    city, large enough to separate markers at neighbourhood zoom. The offset is
    presentational only; it is never a claim about where the house actually is.
    """
    # Golden-angle spiral off a hashed seed: spreads points evenly instead of
    # clumping the way independent random offsets do.
    import math

    # Spiral resolution: distinct positions before two properties can land on
    # the same point. Comfortably above the biggest single-city count (~300
    # locally, and headroom for production) so collisions stay rare.
    positions = 9973

    h = (seed * 2654435761) % 4294967296
    idx = h % positions
    angle = idx * 2.399963229728653  # golden angle in radians
    radius = spread * math.sqrt((idx + 0.5) / positions)
    return radius * math.cos(angle), radius * math.sin(angle)
