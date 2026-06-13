from django.db import models


class InterestRequest(models.Model):
    """An 'Expression of Interest' submitted via the form on a property page
    or the floating button on the home page."""

    SOURCE_PROPERTY = "property"
    SOURCE_HOME = "home"
    SOURCE_CHOICES = [
        (SOURCE_PROPERTY, "Property page"),
        (SOURCE_HOME, "Home page"),
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
        verbose_name = "Interest request"
        verbose_name_plural = "Interest requests"

    def __str__(self):
        return f"{self.name} <{self.email}> @ {self.created_at:%Y-%m-%d %H:%M}"


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
