"""What our own database knows that nobody else does.

The strongest content this account can post is true, specific and impossible to
get anywhere else: how many houses under twenty thousand dollars appeared this
week, where they are, what the cheapest one on the site is today. It is also the
safest — the numbers come out of a query, the copy is written here in Python,
and no model gets near a figure it could round wrong.
"""

import logging
import re
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from inventory.models import Property
from social.constants import JAPAN_PREFECTURES
from social.content.material import Material
from social.models import SocialPost
from social.utils import _clean_location

logger = logging.getLogger(__name__)

# Prices are stored in 万 (man = ¥10,000) units, so this is ¥3,000,000.
CHEAP_THRESHOLD_MAN = 300


def _short_place(location):
    """City and prefecture, not the whole scraped address.

    "6286-4 Oaza Shiura Takahama, Tsukumi City, Oita Prefecture" is the address
    on the listing; nobody reads that as a place. City plus prefecture is what
    someone can actually picture and search for.
    """
    cleaned = _clean_location(location or "")
    prefecture = _prefecture(cleaned)
    city = re.search(r"([A-Za-z][A-Za-z\-]+(?:\s+[A-Z][A-Za-z\-]+)?)\s+City", cleaned)
    if city and prefecture:
        return f"{city.group(1)} City, {prefecture}"
    if prefecture:
        return prefecture
    return cleaned


def _live():
    """Listings we would actually send someone to."""
    return Property.objects.filter(show_in_front=True, price__gt=0)


def _added_this_week():
    since = timezone.now() - timedelta(days=7)
    count = _live().filter(created_at__gte=since).count()
    if count < 3:
        # Fewer than three is not a story, and "2 houses added" reads as a
        # quiet week rather than an opportunity.
        return None

    cheap = _live().filter(created_at__gte=since, price__lte=CHEAP_THRESHOLD_MAN).count()
    top_areas = (
        _live()
        .filter(created_at__gte=since)
        .exclude(location="")
        .values("location")
        .annotate(n=Count("id"))
        .order_by("-n")[:3]
    )
    areas = [_clean_location(row["location"]) for row in top_areas]
    areas = [area for area in areas if area][:3]

    body = f"{count} new houses went up on the site this week."
    if cheap:
        body += f"\n{cheap} of them are under ¥{CHEAP_THRESHOLD_MAN * 10000:,}."
    if areas:
        body += "\nMostly around " + ", ".join(areas) + "."
    body += "\nAll of them are on the site now, with photos and the full details."

    caption = f"{count} houses went up this week"
    if cheap:
        caption += f", {cheap} of them under ¥{CHEAP_THRESHOLD_MAN * 10000:,}"
    caption += ". They are all on the site now, with photos and the details."

    return Material(
        kind=SocialPost.KIND_DATA,
        key="data:added-this-week",
        headline=f"{count} new houses this week",
        facts=[body],
        prewritten_body=body,
        prewritten_caption=caption,
        medium="single",
        eyebrow="This week",
        cooldown_days=7,
    )


def _cheapest_now():
    cheapest = _live().order_by("price").first()
    if not cheapest:
        return None
    location = _short_place(cheapest.location) or "rural Japan"
    price = cheapest.get_price_for_front

    body = (
        f"{location}.\n"
        "It will not be the cheapest for long — these move, and the listing "
        "goes when it sells.\n"
        "Full details and photos are on the site."
    )
    # The headline already says the price, so the caption does not repeat it.
    caption = (
        f"The cheapest house on the site right now, in {location}. These do not "
        "sit around — the listing goes when it sells."
    )
    return Material(
        kind=SocialPost.KIND_DATA,
        key="data:cheapest-now",
        headline=f"{price}",
        facts=[body],
        prewritten_body=body,
        prewritten_caption=caption,
        medium="single",
        eyebrow="Cheapest on the site",
        cooldown_days=21,
        meta={"location": location},
    )


def _under_threshold_count():
    count = _live().filter(price__lte=CHEAP_THRESHOLD_MAN).count()
    if count < 5:
        return None
    body = (
        f"There are {count} houses on the site under "
        f"¥{CHEAP_THRESHOLD_MAN * 10000:,} today.\n"
        "Some need everything doing. Some are liveable now. The price usually "
        "tells you which.\n"
        "Every one of them is listed with photos and the full details."
    )
    return Material(
        kind=SocialPost.KIND_DATA,
        key="data:under-threshold",
        headline=f"{count} houses under ¥{CHEAP_THRESHOLD_MAN * 10000:,}",
        facts=[body],
        prewritten_body=body,
        medium="single",
        eyebrow="Right now",
        cooldown_days=21,
    )


def _prefecture(location):
    """The prefecture named in a scraped address, or None.

    Grouping on the raw `location` string produced headlines like "5 houses in
    Sena 7-chome, Aoi Ward, Shizuoka City, Shizuoka Prefecture" — technically a
    group, useless as a place. Nobody searches for a chome.
    """
    if not location:
        return None
    for prefecture in JAPAN_PREFECTURES:
        if re.search(rf"\b{prefecture}\b", location, re.IGNORECASE):
            return prefecture
    return None


def _where_they_are():
    counts = {}
    for location in _live().exclude(location="").values_list("location", flat=True):
        prefecture = _prefecture(location)
        if prefecture:
            counts[prefecture] = counts.get(prefecture, 0) + 1
    if not counts:
        return None

    area, count = max(counts.items(), key=lambda pair: pair[1])
    if count < 3:
        return None

    body = (
        f"{area} has {count} houses listed with us right now — more than any "
        "other prefecture on the site.\n"
        "That usually means one thing: people are leaving faster than anyone is "
        "arriving.\n"
        "Which is exactly why the prices look the way they do."
    )
    return Material(
        kind=SocialPost.KIND_DATA,
        key=f"data:area-{area.lower()}",
        headline=f"{count} houses in {area}",
        facts=[body],
        prewritten_body=body,
        medium="single",
        eyebrow="Where they are",
        cooldown_days=30,
        meta={"location": area},
    )


BUILDERS = [
    _added_this_week,
    _cheapest_now,
    _under_threshold_count,
    _where_they_are,
]


def gather():
    """Every stats post the database can currently support.

    A builder returning None means the data does not justify a post — too few
    listings, too quiet a week. That is a normal outcome, not an error: the
    alternative is posting "2 new houses this week" and looking automated.
    """
    materials = []
    for builder in BUILDERS:
        try:
            material = builder()
        except Exception as exc:
            logger.warning("Stats builder %s failed: %s", builder.__name__, exc)
            continue
        if material:
            materials.append(material)
    logger.info("Stats: %s postable materials", len(materials))
    return materials
