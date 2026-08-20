from django.contrib import admin
from inventory.models import GeocodedPlace, Property, PropertyImage
from django.db import models
from django.utils.html import format_html


@admin.register(GeocodedPlace)
class GeocodedPlaceAdmin(admin.ModelAdmin):
    """Mostly for spotting bad matches — `display_name` shows what the geocoder
    actually thought the key meant, which is how you catch a pin in the wrong
    prefecture."""

    list_display = ("key", "latitude", "longitude", "display_name", "attempts",
                    "checked_at")
    list_filter = ("attempts",)
    search_fields = ("key", "display_name")
    readonly_fields = ("checked_at",)
    ordering = ("key",)


class PropertyImageInline(admin.TabularInline):
    model = PropertyImage
    extra = 1
    max_num = 10


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = [
        "image_tag",
        "created_at",
        "title",
        "price",
        "floor_plan",
        "building_area",
        "land_area",
        "construction",
        "show_in_front",
        "featured",
        "premium",
        "station",
    ]
    # Filters on the derived transit fields, because "which listings are near a
    # station" is the question the parsing exists to answer and it should be
    # answerable without a shell.
    list_filter = ["needs_bus", "show_in_front", "featured", "premium"]
    search_fields = ["title", "nearest_station", "location"]

    @admin.display(description="Station", ordering="station_walk_minutes")
    def station(self, obj):
        if obj.station_walk_minutes is not None:
            return f"{obj.nearest_station} · {obj.station_walk_minutes} min walk"
        if obj.station_distance_km is not None:
            return f"{obj.nearest_station} · {obj.station_distance_km} km"
        return "bus only" if obj.needs_bus else "—"

    inlines = [
        PropertyImageInline,
    ]

    def image_tag(self, obj):
        if obj.images.first():
            return format_html(
                '<img src="{}" style="max-height: 150px; max-width: 150px;" />'.format(
                    obj.images.first().file
                )
            )

    image_tag.short_description = "Image"
