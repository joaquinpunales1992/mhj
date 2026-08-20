from social.models import SocialPost
from django.contrib import admin


@admin.register(SocialPost)
class SocialPostAdmin(admin.ModelAdmin):
    """The posting log, now with what each post earned.

    The numbers are a snapshot written by `manage.py reel_insights`; a dash
    means that post has never been fetched, which is a different thing from a
    post that got nothing. Sorting by views here is the quickest way to see
    which caption angle and which hook are worth keeping.
    """

    list_display = [
        "datetime",
        "social_media",
        "content_type",
        "views",
        "reach",
        "saves",
        "shares",
        "watch_time",
        "caption_angle",
        "property_url",
    ]
    search_fields = ["caption", "property_url", "social_media", "caption_angle",
                     "media_id"]
    list_filter = ["social_media", "content_type", "caption_angle", "datetime"]
    ordering = ["-datetime"]
    date_hierarchy = "datetime"
    readonly_fields = ["datetime", "media_id", "views", "reach", "likes",
                       "comments_count", "saves", "shares", "total_interactions",
                       "avg_watch_time_ms", "insights_fetched_at"]

    @admin.display(description="Watch", ordering="avg_watch_time_ms")
    def watch_time(self, obj):
        if obj.avg_watch_time_ms is None:
            return "—"
        return f"{obj.avg_watch_time_ms / 1000:.1f}s"
