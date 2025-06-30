from django import template
from inventory.models import Property
from inventory.constants import MAX_RELATED_PROPERTIES
from django.db.models import QuerySet

register = template.Library()


@register.simple_tag
def find_related_properties(property_pk: int) -> QuerySet:
    return Property.objects.filter(location__icontains=Property.objects.get(pk=property_pk).get_location_for_front(), premium=False, images__isnull=False).exclude(pk=property_pk).order_by('price')[:MAX_RELATED_PROPERTIES]
