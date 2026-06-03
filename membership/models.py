from django.db import models


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
