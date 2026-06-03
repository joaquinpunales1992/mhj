from django.contrib import admin

from membership.models import PremiumRequest


@admin.register(PremiumRequest)
class PremiumRequestAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user_email", "property_url", "contacted")
    list_filter = ("contacted", "created_at")
    search_fields = ("user_email", "property_url", "notes")
    list_editable = ("contacted",)
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"
