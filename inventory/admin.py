from django.contrib import admin
from inventory.models import Property, PropertyImage
from django.db import models
from django.utils.html import format_html


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
    ]
    search_fields = ["title", "premium", "featured"]

    inlines = [PropertyImageInline,]

    def image_tag(self, obj):
        if obj.images.first():
            return format_html('<img src="{}" style="max-height: 150px; max-width: 150px;" />'.format(obj.images.first().file))

    image_tag.short_description = 'Image'




    


