from django import template
import random
from inventory.models import Property
from inventory.images import (  # noqa: F401
    thumb_url,
    WIDTH_CARD,
    WIDTH_GALLERY,
    QUALITY_CARD,
    QUALITY_GALLERY,
)
from inventory.constants import MAX_RELATED_PROPERTIES
from django.db.models import QuerySet

register = template.Library()


@register.filter
def thumb(url, spec=WIDTH_CARD):
    """A resized WebP version of a remote listing photo.

    `spec` is a width, or "width,quality" — the second form exists because the
    right quality depends on how far the image is being downscaled, and asking
    for too high a quality at near-native width produces a file bigger than the
    original. See inventory/images.py for the measurements.

        {{ image.file|thumb }}            600w, card quality
        {{ image.file|thumb:1000 }}       1000w, card quality
        {{ image.file|thumb:"1000,70" }}  1000w, gallery quality

    Callers must keep the ORIGINAL url for full-size use — poptrox opens the
    <a href> in its lightbox, and structured data / og:image must point at the
    real file, not a thumbnail.
    """
    width, _, quality = str(spec).partition(",")
    return thumb_url(
        url,
        width.strip() or WIDTH_CARD,
        quality.strip() or QUALITY_CARD,
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