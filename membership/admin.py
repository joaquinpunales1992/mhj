from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.html import format_html

from membership.models import (
    DeskReportOrder,
    InspectionRequest,
    Consultation,
    InterestRequest,
    PremiumRequest,
    ProAttempt,
    PropertyView,
    SavedProperty,
    SavedSearch,
    Subscription,
)
from membership.utils import refer_lead_to_agent


@admin.register(InterestRequest)
class InterestRequestAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "name",
        "email",
        "status_badge",
        "age",
        "budget",
        "timeline",
        "regions",
        "referred_to",
    )
    list_filter = (
        "status",
        "lost_reason",
        "source",
        "timeline",
        "visited_japan",
        "created_at",
    )
    list_editable = ()
    search_fields = ("name", "email", "message", "regions", "property_url", "notes")
    readonly_fields = ("created_at", "status_changed_at", "referred_at", "closed_at")
    date_hierarchy = "created_at"
    actions = ("action_refer_to_agent", "action_mark_lost_no_response")

    fieldsets = (
        (
            "Lead",
            {"fields": ("name", "email", "message", "created_at", "source")},
        ),
        (
            "What they told us",
            {"fields": ("regions", "budget", "timeline", "visited_japan", "property_url")},
        ),
        (
            "Where it stands",
            {
                "fields": (
                    "status",
                    "status_changed_at",
                    "referred_to",
                    "referred_at",
                    "lost_reason",
                    "closed_at",
                    "notes",
                ),
                "description": (
                    "Set <b>Dead</b> plus a reason the moment a lead stops moving. "
                    "An honest dead lead teaches you something; a lead left on "
                    "'New' forever teaches you nothing."
                ),
            },
        ),
    )

    # Colour-coded so a stalled funnel is visible at a glance instead of
    # requiring you to read every row.
    STATUS_COLOURS = {
        InterestRequest.STATUS_NEW: "#b45309",
        InterestRequest.STATUS_CALL_BOOKED: "#2563eb",
        InterestRequest.STATUS_CALL_DONE: "#2563eb",
        InterestRequest.STATUS_REFERRED: "#7c3aed",
        InterestRequest.STATUS_VIEWING: "#7c3aed",
        InterestRequest.STATUS_OFFER: "#7c3aed",
        InterestRequest.STATUS_WON: "#15803d",
        InterestRequest.STATUS_LOST: "#9ca3af",
    }

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        label = obj.get_status_display()
        if obj.status == InterestRequest.STATUS_LOST and obj.lost_reason:
            label = f"{label} — {obj.get_lost_reason_display()}"
        return format_html(
            '<span style="color:{};font-weight:600">{}</span>',
            self.STATUS_COLOURS.get(obj.status, "#333"),
            label,
        )

    @admin.display(description="Age", ordering="status_changed_at")
    def age(self, obj):
        days = obj.days_in_status
        if days is None:
            return "—"
        if obj.is_stale:
            return format_html(
                '<span style="color:#b91c1c;font-weight:600">{}d ⚠</span>', days
            )
        return f"{days}d"

    @admin.action(description="Refer selected leads to the licensed agent")
    def action_refer_to_agent(self, request, queryset):
        # Only forward leads that have actually been worked. Referring raw
        # form submissions is the behaviour that produced a 0% close rate.
        eligible = queryset.filter(
            status__in=(
                InterestRequest.STATUS_CALL_DONE,
                InterestRequest.STATUS_REFERRED,
            )
        )
        skipped = queryset.count() - eligible.count()

        sent = 0
        for lead in eligible:
            if refer_lead_to_agent(lead):
                sent += 1
                lead.status = InterestRequest.STATUS_REFERRED
                lead.save()

        if sent:
            self.message_user(request, f"Referred {sent} lead(s) to the agent.")
        if not sent and eligible:
            self.message_user(
                request,
                "No referral sent — AGENT_NOTIFICATION_EMAILS is empty. Set it "
                "in the server .env once a referral agreement is in writing.",
                level=messages.WARNING,
            )
        if skipped:
            self.message_user(
                request,
                f"Skipped {skipped} lead(s) not yet at 'Consultation done'. "
                "Work the lead before handing it over.",
                level=messages.WARNING,
            )

    @admin.action(description="Mark selected as dead — went quiet")
    def action_mark_lost_no_response(self, request, queryset):
        updated = 0
        for lead in queryset.exclude(status=InterestRequest.STATUS_WON):
            lead.status = InterestRequest.STATUS_LOST
            lead.lost_reason = InterestRequest.LOST_NO_RESPONSE
            lead.save()
            updated += 1
        self.message_user(request, f"Marked {updated} lead(s) as dead.")

    def changelist_view(self, request, extra_context=None):
        """Put the funnel counts above the list so the drop-off point is the
        first thing you see, rather than something you have to go and query."""
        counts = dict(
            InterestRequest.objects.values_list("status")
            .annotate(n=Count("id"))
            .values_list("status", "n")
        )
        labels = dict(InterestRequest.STATUS_CHOICES)
        summary = [
            (labels[s], counts.get(s, 0)) for s in InterestRequest.STATUS_ORDER
        ]
        summary.append(
            (labels[InterestRequest.STATUS_LOST], counts.get(InterestRequest.STATUS_LOST, 0))
        )

        lost_breakdown = list(
            InterestRequest.objects.filter(status=InterestRequest.STATUS_LOST)
            .exclude(lost_reason="")
            .values_list("lost_reason")
            .annotate(n=Count("id"))
            .values_list("lost_reason", "n")
        )
        lost_labels = dict(InterestRequest.LOST_REASON_CHOICES)

        extra_context = extra_context or {}
        extra_context["funnel_summary"] = summary
        extra_context["lost_breakdown"] = [
            (lost_labels.get(reason, reason), n) for reason, n in lost_breakdown
        ]
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(PremiumRequest)
class PremiumRequestAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user_email", "property_url", "contacted")
    list_filter = ("contacted", "created_at")
    search_fields = ("user_email", "property_url", "notes")
    list_editable = ("contacted",)
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"


@admin.register(SavedProperty)
class SavedPropertyAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "property")
    list_filter = ("created_at",)
    search_fields = ("user__email", "property__title")
    date_hierarchy = "created_at"
    raw_id_fields = ("property",)


@admin.register(SavedSearch)
class SavedSearchAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "city", "price", "notify")
    list_filter = ("notify", "city", "created_at")
    search_fields = ("user__email", "city")
    date_hierarchy = "created_at"


class SignupStatsAdmin(admin.ModelAdmin):
    """Read-only view of account signups over time.

    Exists to answer one question before any paid tier gets built: are free
    accounts being created at a rate that could support a subscription? Without
    a number here that decision is guesswork.
    """

    list_display = ("email", "date_joined", "saved_count", "search_count", "last_login")
    list_filter = ("date_joined", "is_active")
    search_fields = ("email",)
    date_hierarchy = "date_joined"
    ordering = ("-date_joined",)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                _saved=Count("saved_properties", distinct=True),
                _searches=Count("saved_searches", distinct=True),
            )
        )

    @admin.display(description="Saved", ordering="_saved")
    def saved_count(self, obj):
        return obj._saved

    @admin.display(description="Searches", ordering="_searches")
    def search_count(self, obj):
        return obj._searches

    def has_add_permission(self, request):
        return False

    def changelist_view(self, request, extra_context=None):
        now = timezone.now()
        users = User.objects.all()
        extra_context = extra_context or {}
        extra_context["signup_summary"] = [
            ("Total accounts", users.count()),
            ("Last 7 days", users.filter(date_joined__gte=now - timedelta(days=7)).count()),
            ("Last 30 days", users.filter(date_joined__gte=now - timedelta(days=30)).count()),
            ("With a saved item", users.filter(
                Q(saved_properties__isnull=False) | Q(saved_searches__isnull=False)
            ).distinct().count()),
        ]
        return super().changelist_view(request, extra_context=extra_context)


# Re-register User through the stats admin so the counts sit where you'd look
# for them, rather than on a separate page.
admin.site.unregister(User)
admin.site.register(User, SignupStatsAdmin)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    """Also the manual override: setting status to Active grants Pro without
    PayPal, which is how you comp someone or fix a webhook that didn't land."""

    list_display = ("user", "status", "current_period_end", "active",
                    "paypal_subscription_id", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("user__email", "paypal_subscription_id")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(boolean=True, description="Access")
    def active(self, obj):
        return obj.is_active


@admin.register(ProAttempt)
class ProAttemptAdmin(admin.ModelAdmin):
    """Everybody who tried to pay for Pro, whether or not Pro was on sale.

    Read-only and deliberately next to Subscriptions rather than inside it: a
    Subscription row is an entitlement the site gates on, while a row here is
    just a click. The two must not be filed in the same drawer.

    The number to look at is "wanted it while it was unbuyable" in the summary.
    If that keeps climbing, the PayPal integration is the thing to finish.
    """

    list_display = ("created_at", "who", "source", "purchasable", "from_url")
    list_filter = ("source", "billing_configured", "created_at")
    search_fields = ("email", "user__email", "from_url")
    date_hierarchy = "created_at"
    readonly_fields = ("user", "email", "source", "billing_configured",
                       "from_url", "created_at")

    @admin.display(description="Who", ordering="email")
    def who(self, obj):
        return obj.email or (obj.user and obj.user.username) or "anonymous"

    @admin.display(boolean=True, description="Could pay", ordering="billing_configured")
    def purchasable(self, obj):
        return obj.billing_configured

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        now = timezone.now()
        attempts = ProAttempt.objects.all()
        extra_context = extra_context or {}
        extra_context["pro_attempt_summary"] = [
            ("Attempts, all time", attempts.count()),
            ("Last 7 days", attempts.filter(
                created_at__gte=now - timedelta(days=7)).count()),
            ("Last 30 days", attempts.filter(
                created_at__gte=now - timedelta(days=30)).count()),
            ("Wanted it while it was unbuyable", attempts.filter(
                billing_configured=False).count()),
            ("Distinct people", attempts.exclude(email="").values(
                "email").distinct().count()),
        ]
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(PropertyView)
class PropertyViewAdmin(admin.ModelAdmin):
    """Read-only: what each member has spent their allowance on."""

    list_display = ("created_at", "user", "property")
    list_filter = ("created_at",)
    search_fields = ("user__email",)
    date_hierarchy = "created_at"
    raw_id_fields = ("property",)

    def has_add_permission(self, request):
        return False


@admin.register(Consultation)
class ConsultationAdmin(admin.ModelAdmin):
    """The call diary.

    Ordered soonest-first and defaulted to the bookings that still matter, so
    opening this is "what is coming up" rather than "everything that ever
    happened". Expired holds are listed as such rather than hidden, because an
    abandoned checkout is a lead worth chasing.
    """

    list_display = (
        "starts_at_agent",
        "starts_at_visitor",
        "name",
        "email",
        "state",
        "amount_paid",
        "listing_link",
    )
    list_filter = ("status", "starts_at")
    search_fields = ("name", "email", "paypal_order_id", "paypal_capture_id", "notes")
    readonly_fields = (
        "created_at", "paid_at", "paypal_order_id", "paypal_capture_id",
        "amount", "currency", "visitor_timezone",
    )
    date_hierarchy = "starts_at"
    ordering = ("-starts_at",)

    @admin.display(description=f"When ({settings.CONSULT_TIMEZONE})", ordering="starts_at")
    def starts_at_agent(self, obj):
        local = obj.starts_at.astimezone(ZoneInfo(settings.CONSULT_TIMEZONE))
        return f"{local:%a %d %b %Y, %H:%M}"

    @admin.display(description="Their local time")
    def starts_at_visitor(self, obj):
        if not obj.visitor_timezone:
            return "—"
        try:
            local = obj.starts_at.astimezone(ZoneInfo(obj.visitor_timezone))
        except Exception:
            return obj.visitor_timezone
        return f"{local:%a %d %b, %H:%M} ({obj.visitor_timezone})"

    @admin.display(description="Status")
    def state(self, obj):
        if obj.is_expired_hold:
            return format_html('<span style="color:#999">abandoned checkout</span>')
        colour = {
            obj.STATUS_PAID: "#2f7a34",
            obj.STATUS_COMPLETED: "#2f7a34",
            obj.STATUS_HOLD: "#b8860b",
            obj.STATUS_CANCELLED: "#999",
        }.get(obj.status, "#333")
        return format_html('<span style="color:{}">{}</span>', colour, obj.get_status_display())

    @admin.display(description="Paid")
    def amount_paid(self, obj):
        if obj.status not in (obj.STATUS_PAID, obj.STATUS_COMPLETED):
            return "—"
        return f"{obj.amount} {obj.currency}"

    @admin.display(description="Property")
    def listing_link(self, obj):
        if not obj.listing:
            return "—"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">{}</a>',
            obj.listing.get_public_url,
            obj.listing.get_location_for_front(),
        )


@admin.register(DeskReportOrder)
class DeskReportOrderAdmin(admin.ModelAdmin):
    """Paid desk reports, and how long each has been owed.

    The list is a queue, not a report: every paid row is work outstanding until
    it is marked delivered, and `owed_for` is there so nothing quietly ages.
    """

    list_display = ("created_at", "email", "listing_location", "status",
                    "owed_for", "amount")
    list_filter = ("status", "created_at")
    search_fields = ("email", "name", "listing_url", "listing_location",
                     "paypal_order_id")
    list_editable = ("status",)
    readonly_fields = ("created_at", "paid_at", "paypal_order_id",
                       "paypal_capture_id", "amount", "currency", "user",
                       "buyer_notes")
    date_hierarchy = "created_at"

    @admin.display(description="Owed for", ordering="paid_at")
    def owed_for(self, obj):
        days = obj.days_owed
        if days is None:
            return "—"
        colour = "#b91c1c" if days >= 3 else "#92400e" if days >= 2 else "#166534"
        return format_html(
            '<b style="color:{}">{} day{}</b>', colour, days, "" if days == 1 else "s"
        )

    def save_model(self, request, obj, form, change):
        # Stamp the delivery date from the status change, so "sent" and "when"
        # cannot disagree.
        if obj.status == DeskReportOrder.STATUS_DELIVERED and obj.delivered_at is None:
            obj.delivered_at = timezone.now()
        if obj.status != DeskReportOrder.STATUS_DELIVERED:
            obj.delivered_at = None
        super().save_model(request, obj, form, change)


@admin.register(InspectionRequest)
class InspectionRequestAdmin(admin.ModelAdmin):
    """The inspection to-do list.

    Every row needs a human: confirm the listing is still there, confirm the agent
    will allow access, get a price, reply. Defaults to newest first and shows
    whether a reply is owed, because a request that sits here loses its value when
    the house sells.
    """

    list_display = ("created_at", "reply_owed", "email", "where", "status", "quoted_amount")
    list_filter = ("status", "created_at")
    search_fields = ("email", "name", "notes", "internal_notes",
                     "listing_location", "listing_url")
    readonly_fields = ("created_at", "updated_at", "user", "listing",
                       "listing_url", "listing_location", "notes", "email", "name")
    list_editable = ("status", "quoted_amount")
    date_hierarchy = "created_at"
    fieldsets = (
        ("The request", {
            "fields": ("created_at", "email", "name", "user", "notes"),
        }),
        ("Property", {
            "fields": ("listing", "listing_location", "listing_url"),
            "description": "Stored flat as well as by link, because listings get "
                           "delisted and the link then goes blank.",
        }),
        ("Your handling", {
            "fields": ("status", "quoted_amount", "internal_notes", "updated_at"),
        }),
    )

    @admin.display(boolean=True, description="Reply owed")
    def reply_owed(self, obj):
        return obj.needs_reply

    @admin.display(description="Property")
    def where(self, obj):
        if obj.listing_url:
            return format_html(
                '<a href="{}" target="_blank" rel="noopener">{}</a>',
                obj.listing_url, obj.listing_location or "listing",
            )
        return obj.listing_location or "—"
