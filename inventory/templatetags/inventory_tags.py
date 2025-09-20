from django import template
import random
from inventory.models import Property
from inventory.constants import MAX_RELATED_PROPERTIES
from django.db.models import QuerySet

register = template.Library()


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