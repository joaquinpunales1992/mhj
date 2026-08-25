from django.contrib import admin
from django.utils.html import format_html

from social.models import ContentDraft, SocialPost


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


@admin.register(ContentDraft)
class ContentDraftAdmin(admin.ModelAdmin):
    """The record of what the community manager said, and what it skipped.

    With SOCIAL_REQUIRE_APPROVAL off — the default — this is a log, not a gate:
    the planner posts on its own and writes a row here for each one, including
    the ones it decided against. Rows left in Draft with nothing posted are
    where to look when a run went quiet.

    Turn SOCIAL_REQUIRE_APPROVAL on and the same table becomes an approval
    queue: nothing reaches an audience until it is approved here.
    """

    list_display = ["created_at", "kind", "status", "flag", "question",
                    "source", "posted_at"]
    list_filter = ["status", "kind", "needs_review", "source"]
    search_fields = ["question", "answer", "caption", "key"]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at", "posted_at", "card_preview"]
    fields = ["kind", "status", "source", "needs_review", "key", "question",
              "answer", "caption", "card_preview", "card_paths",
              "created_at", "updated_at", "posted_at"]
    actions = ["approve_drafts", "reject_drafts"]

    @admin.display(description="⚑")
    def flag(self, obj):
        return "check" if obj.needs_review else ""

    @admin.display(description="Cards")
    def card_preview(self, obj):
        """Thumbnails, when the cards happen to be reachable over /media/.

        Falls back to listing the paths: in production the working copies live
        outside anything served, and a broken <img> would be a worse answer
        than the filename.
        """
        import os

        from django.conf import settings

        html = []
        for path in obj.card_path_list:
            rel = os.path.relpath(path, settings.MEDIA_ROOT)
            if rel.startswith(".."):
                html.append(f"<div>{path}</div>")
            else:
                url = f"{settings.MEDIA_URL}{rel}"
                html.append(
                    f'<a href="{url}" target="_blank">'
                    f'<img src="{url}" style="height:240px;margin:0 8px 8px 0;'
                    'border:1px solid #444"></a>'
                )
        return format_html("".join(html)) if html else "—"

    @admin.action(description="Approve — redraw cards from the edited answer")
    def approve_drafts(self, request, queryset):
        """Only relevant with SOCIAL_REQUIRE_APPROVAL on.

        With autonomy on, which is the default, this table is a log of what went
        out rather than a gate in front of it. Approving still redraws the cards
        from the edited text, so a draft you fixed by hand can be released.
        """
        from social.content.publisher import CARD_DIR
        from social.content.cards import render_cards

        approved = 0
        for draft in queryset.exclude(status=ContentDraft.STATUS_POSTED):
            try:
                from django.utils import timezone

                stamp = timezone.now().strftime("%Y%m%d%H%M%S")
                paths = render_cards(
                    draft.question, draft.answer, CARD_DIR,
                    f"{draft.key.replace(':', '-')}-{stamp}",
                    eyebrow=draft.get_kind_display(), swipe_hint="swipe →",
                )
            except Exception as exc:
                self.message_user(
                    request, f"Draft {draft.pk}: could not redraw cards ({exc})",
                    level="ERROR",
                )
                continue
            draft.card_paths = "\n".join(paths)
            draft.status = ContentDraft.STATUS_APPROVED
            draft.save(update_fields=["card_paths", "status", "updated_at"])
            approved += 1
        self.message_user(request, f"{approved} draft(s) approved.")

    @admin.action(description="Reject")
    def reject_drafts(self, request, queryset):
        updated = queryset.exclude(status=ContentDraft.STATUS_POSTED).update(
            status=ContentDraft.STATUS_REJECTED
        )
        self.message_user(request, f"{updated} draft(s) rejected.")
