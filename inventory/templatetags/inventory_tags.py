from django import template
import random
from urllib.parse import quote
from inventory.models import Property
from inventory.constants import MAX_RELATED_PROPERTIES
from django.db.models import QuerySet

register = template.Library()


# Listing photos are hotlinked from the source sites (suumo.jp, homes.jp) at
# whatever size they were published — routinely 1000x750 and 130-250KB — to fill
# a card that renders about 480px wide. On the home page that was 2.8MB of
# images, 74% of the page, and the reason LCP measured 23s on a throttled phone.
#
# Rather than store our own copies (a 10k-property backfill on a low-RAM box),
# route the URL through a resizing proxy that fetches, resizes and re-encodes to
# WebP on demand. Measured on a real listing photo: 259KB JPEG -> 48KB WebP.
#
# Callers must keep the ORIGINAL url for any full-size use — poptrox opens the
# <a href> in its lightbox, and that should stay full quality.
THUMB_PROXY = "https://wsrv.nl/"


@register.filter
def thumb(url, width=700):
    """A resized WebP version of a remote image URL.

    Returns the input unchanged for anything that is not a remote http(s) URL,
    so local {% static %} paths and empty values pass straight through.

    `we` prevents the proxy enlarging a source that is already smaller than the
    requested width — some listings ship 300px photos and upscaling them would
    add bytes to make them blurrier.
    """
    if not url:
        return url
    url = str(url)
    if not url.startswith(("http://", "https://")):
        return url
    try:
        width = int(width)
    except (TypeError, ValueError):
        width = 700
    return (
        f"{THUMB_PROXY}?url={quote(url, safe='')}"
        f"&w={width}&output=webp&q=80&we"
    )


@register.simple_tag
def find_related_properties(property_pk: int) -> QuerySet:
    queryset = Property.objects.filter(
        location__icontains=Property.objects.get(
            pk=property_pk
        ).get_location_for_front(),
        premium=False,
        images__isnull=False,
    ).exclude(pk=property_pk).distinct()
    # Get all matching pks, shuffle, and slice
    pks = list(queryset.values_list('pk', flat=True))
    random.shuffle(pks)
    selected_pks = pks[:MAX_RELATED_PROPERTIES]
    return Property.objects.filter(pk__in=selected_pks)


@register.filter
def random_choice(value):
    """Returns a random choice from a comma-separated string"""
    choices = [choice.strip() for choice in value.split(',')]
    return random.choice(choices)