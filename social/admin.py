from social.models import SocialPost
from django.contrib import admin

@admin.register(SocialPost)
class SocialPostAdmin(admin.ModelAdmin):
    list_display = [
        "caption",
        "datetime",
        "property_url",
        "social_media",
    ]
    search_fields = ["caption", "property_url", "social_media"]
    list_filter = ["social_media"]
    ordering = ["-datetime"]
