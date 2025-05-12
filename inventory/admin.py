from django.contrib import admin
from inventory.models import Property, PropertyImage
from django.db import models


class PropertyImageInline(admin.TabularInline):
    model = PropertyImage
    extra = 1
    max_num = 10

@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = [
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



    


