from django.db import models
from django.utils import timezone


class InterestRequest(models.Model):
    """An 'Expression of Interest' submitted via the form on a property page
    or the floating button on the home page."""

    SOURCE_PROPERTY = "property"
    SOURCE_HOME = "home"
    SOURCE_CHOICES = [
        (SOURCE_PROPERTY, "Property page"),
        (SOURCE_HOME, "Home page"),
    ]

    # Lead lifecycle. A single ordered field rather than a pile of booleans so
    # "where do leads die?" is one GROUP BY instead of a guess. Order matters:
    # STATUS_ORDER below relies on it for the funnel summary.
    STATUS_NEW = "new"
    STATUS_CALL_BOOKED = "call_booked"
    STATUS_CALL_DONE = "call_done"
    STATUS_REFERRED = "referred"
    STATUS_VIEWING = "viewing"
    STATUS_OFFER = "offer"
    STATUS_WON = "won"
    STATUS_LOST = "lost"
    STATUS_CHOICES = [
        (STATUS_NEW, "1. New — not yet worked"),
        (STATUS_CALL_BOOKED, "2. Consultation booked"),
        (STATUS_CALL_DONE, "3. Consultation done"),
        (STATUS_REFERRED, "4. Referred to agent"),
        (STATUS_VIEWING, "5. Viewing arranged"),
        (STATUS_OFFER, "6. Offer made"),
        (STATUS_WON, "7. Purchase closed"),
        (STATUS_LOST, "✕ Dead"),
    ]
    # Progression order for the funnel summary; STATUS_LOST is deliberately
    # excluded because a dead lead exits the funnel rather than advancing.
    STATUS_ORDER = [
        STATUS_NEW,
        STATUS_CALL_BOOKED,
        STATUS_CALL_DONE,
        STATUS_REFERRED,
        STATUS_VIEWING,
        STATUS_OFFER,
        STATUS_WON,
    ]

    # Why a lead died. This is the field the whole exercise exists for — without
    # it you can see that leads die but never why, and the four causes below
    # have four completely different fixes.
    LOST_NO_RESPONSE = "no_response"
    LOST_PRICE = "price"
    LOST_FINANCING = "financing"
    LOST_COLD_FEET = "cold_feet"
    LOST_AGENT_SILENT = "agent_silent"
    LOST_PROPERTY_GONE = "property_gone"
    LOST_NOT_SERIOUS = "not_serious"
    LOST_OTHER = "other"
    LOST_REASON_CHOICES = [
        (LOST_NO_RESPONSE, "Went quiet on us"),
        (LOST_PRICE, "Price / total cost"),
        (LOST_FINANCING, "Couldn't finance it"),
        (LOST_COLD_FEET, "Cold feet — decided not to buy"),
        (LOST_AGENT_SILENT, "Agent never worked the lead"),
        (LOST_PROPERTY_GONE, "Property was already sold/delisted"),
        (LOST_NOT_SERIOUS, "Never a serious buyer"),
        (LOST_OTHER, "Other (see notes)"),
    ]

    name = models.CharField(max_length=200)
    email = models.EmailField()
    message = models.TextField(blank=True, default="")
    # Qualification fields collected by the CTA form. Stored as the chosen
    # labels (free of choices constraints so the form copy can evolve without
    # a migration). Region(s) and budget only apply to the home-page form.
    regions = models.CharField(max_length=500, blank=True, default="")
    budget = models.CharField(max_length=50, blank=True, default="")
    timeline = models.CharField(max_length=50, blank=True, default="")
    visited_japan = models.CharField(max_length=10, blank=True, default="")
    property_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="The property page the request was sent from (blank if from the home page).",
    )
    source = models.CharField(
        max_length=20, choices=SOURCE_CHOICES, default=SOURCE_PROPERTY
    )
    created_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_NEW,
        db_index=True,
        help_text="Where this lead currently sits in the funnel.",
    )
    status_changed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set automatically whenever status changes — drives the "
        "'stale' warning in the list view.",
    )
    referred_to = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Which licensed agent this lead was handed to.",
    )
    referred_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set automatically when status first reaches 'Referred to agent'.",
    )
    lost_reason = models.CharField(
        max_length=30,
        choices=LOST_REASON_CHOICES,
        blank=True,
        default="",
        help_text="Only meaningful when status is Dead. Fill this in every "
        "time — it's the only way to learn why the funnel leaks.",
    )
    closed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set automatically when status becomes Purchase closed or Dead.",
    )
    notes = models.TextField(
        blank=True,
        default="",
        help_text="Internal notes (won't be sent to the user).",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Interest request"
        verbose_name_plural = "Interest requests"

    def __str__(self):
        return f"{self.name} <{self.email}> @ {self.created_at:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        # Stamp the lifecycle timestamps here rather than in the admin so they
        # stay correct however the status is changed — admin, shell, or a bulk
        # action. Compare against the stored row to detect an actual change.
        previous_status = None
        if self.pk:
            previous_status = (
                type(self)
                ._default_manager.filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )

        if previous_status != self.status:
            now = timezone.now()
            self.status_changed_at = now
            # referred_at records the *first* handover, so a lead that bounces
            # back to the agent later doesn't lose its original referral date.
            if self.status == self.STATUS_REFERRED and self.referred_at is None:
                self.referred_at = now
            if self.status in (self.STATUS_WON, self.STATUS_LOST):
                self.closed_at = now
            else:
                # Reopening a closed lead clears the close date so "closed in
                # this period" counts stay honest.
                self.closed_at = None

        # A lead that isn't dead has no lost reason; keeping a stale one around
        # would poison the "why do leads die" breakdown.
        if self.status != self.STATUS_LOST:
            self.lost_reason = ""

        super().save(*args, **kwargs)

    @property
    def days_in_status(self):
        """Whole days since the status last changed (falls back to created_at
        for rows that predate lifecycle tracking)."""
        reference = self.status_changed_at or self.created_at
        if not reference:
            return None
        return (timezone.now() - reference).days

    @property
    def is_stale(self):
        """An open lead nobody has touched in a fortnight. Closed leads are
        never stale."""
        if self.status in (self.STATUS_WON, self.STATUS_LOST):
            return False
        days = self.days_in_status
        return days is not None and days >= 14


class PremiumRequest(models.Model):
    """A request submitted via the 'Premium Account' button on a property page."""

    user_email = models.EmailField()
    property_url = models.URLField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    contacted = models.BooleanField(
        default=False,
        help_text="Tick this once you've reached out to the requester.",
    )
    notes = models.TextField(
        blank=True,
        default="",
        help_text="Internal notes (won't be sent to the user).",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Premium request"
        verbose_name_plural = "Premium requests"

    def __str__(self):
        return f"{self.user_email} @ {self.created_at:%Y-%m-%d %H:%M}"


class SavedProperty(models.Model):
    """A property a signed-in user has favourited.

    Favourites, saved searches and the analysis blocks on a property page are
    the free account's reason to exist: they're what a serious buyer wants and
    a casual browser doesn't, so creating one is itself a qualification signal.
    Deliberately gated on a real account rather than the `email` cookie the
    older flows use — the point is to build a list we can actually contact.
    """

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="saved_properties"
    )
    property = models.ForeignKey(
        "inventory.Property", on_delete=models.CASCADE, related_name="saved_by"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # One row per user/property; the toggle endpoint relies on this.
        unique_together = [("user", "property")]
        ordering = ["-created_at"]
        verbose_name = "Saved property"
        verbose_name_plural = "Saved properties"

    def __str__(self):
        return f"{self.user.email} ♥ {self.property_id}"


class SavedSearch(models.Model):
    """A stored city/price filter combination, so a buyer can come back to it.

    Stores the filter values rather than a URL: the URL format has already
    changed once (the map added its own view) and stored links would rot.
    """

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="saved_searches"
    )
    city = models.CharField(max_length=100, blank=True, default="")
    price = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Price bucket key, e.g. 'u50' (see front.views.PRICE_BUCKETS).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    notify = models.BooleanField(
        default=True,
        help_text="Whether the user wants alerts for new matches. Nothing sends "
        "these yet — the flag records intent so we know if it's worth building.",
    )

    class Meta:
        unique_together = [("user", "city", "price")]
        ordering = ["-created_at"]
        verbose_name = "Saved search"
        verbose_name_plural = "Saved searches"

    def __str__(self):
        return f"{self.user.email}: {self.label}"

    @property
    def label(self):
        """Human-readable summary, e.g. "Oita · Under $50k".

        The price bucket definitions live with the views that own the filter
        UI; imported lazily so membership.models stays importable on its own.
        """
        from front.views import PRICE_BUCKETS_BY_KEY

        parts = [self.city or "Anywhere in Japan"]
        bucket = PRICE_BUCKETS_BY_KEY.get(self.price)
        if bucket:
            parts.append(bucket["label"])
        return " · ".join(parts)


class PropertyView(models.Model):
    """One row per property a signed-in user has opened.

    Backs the metered allowance (see membership.metering). Persistent rather
    than session-based so the count follows the account across devices —
    otherwise a new browser would silently reset it.
    """

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="property_views"
    )
    property = models.ForeignKey(
        "inventory.Property", on_delete=models.CASCADE, related_name="views_by"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "property")]
        ordering = ["-created_at"]
        verbose_name = "Property view"
        verbose_name_plural = "Property views"

    def __str__(self):
        return f"{self.user.email} saw {self.property_id}"


class Subscription(models.Model):
    """A user's paid (Pro) subscription, mirrored from PayPal.

    PayPal is the source of truth; this table is the local cache the site gates
    on, kept in step by the webhook. Stored rather than queried live so a
    PayPal outage degrades to "last known state" instead of logging every
    subscriber out.
    """

    STATUS_ACTIVE = "ACTIVE"
    STATUS_APPROVAL_PENDING = "APPROVAL_PENDING"
    STATUS_SUSPENDED = "SUSPENDED"
    STATUS_CANCELLED = "CANCELLED"
    STATUS_EXPIRED = "EXPIRED"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_APPROVAL_PENDING, "Awaiting approval"),
        (STATUS_SUSPENDED, "Suspended (payment failed)"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_EXPIRED, "Expired"),
    ]

    user = models.OneToOneField(
        "auth.User", on_delete=models.CASCADE, related_name="subscription"
    )
    paypal_subscription_id = models.CharField(
        max_length=64, unique=True, help_text="PayPal's subscription id (I-XXXX)."
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_APPROVAL_PENDING
    )
    # Access runs to the end of the paid period even after cancellation, which
    # is what the subscriber paid for and avoids refund arguments.
    current_period_end = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Subscription"
        verbose_name_plural = "Subscriptions"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.email}: {self.status}"

    @property
    def is_active(self):
        if self.status == self.STATUS_ACTIVE:
            return True
        # A cancelled subscription still grants access until the period it was
        # paid for runs out.
        if self.status in (self.STATUS_CANCELLED, self.STATUS_SUSPENDED):
            return bool(
                self.current_period_end and self.current_period_end > timezone.now()
            )
        return False
