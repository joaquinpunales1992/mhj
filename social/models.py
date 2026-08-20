from django.db import models


class SocialPost(models.Model):
    """
    A model representing a social media post.
    """

    ai_caption = models.TextField(default="", blank=True)
    caption = models.TextField()
    datetime = models.DateTimeField(auto_now_add=True)
    property_url = models.URLField(max_length=255, blank=True)
    social_media = models.CharField(
        max_length=50, choices=[("facebook", "Facebook"), ("instagram", "Instagram")]
    )
    content_type = models.CharField(
        max_length=50, choices=[("post", "Post"), ("reel", "Reel")], default="post"
    )
    sound_track = models.CharField(max_length=255, blank=True, default="")

    # --- What was published, so it can be looked up afterwards -------------
    # Without the published id there is no way to ask Instagram how a post did,
    # and no way to backfill: every post made before this field existed is
    # permanently unattributable. That is the whole reason it is here.
    media_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="Instagram/Facebook id of the published media. Blank means we "
        "cannot fetch insights for this one.",
    )
    # The two things that vary between posts and might explain why one travelled
    # further than another. Stored rather than inferred from the caption text,
    # which the model rewrites every time.
    caption_angle = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Creative direction the caption was written to.",
    )
    overlay_hook = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="The phrase burnt into the video, when there was one.",
    )

    # --- Insights snapshot -------------------------------------------------
    # A snapshot, deliberately overwritten on each refresh rather than kept as a
    # history: the question these answer is "which kind of post works", and for
    # that the latest number for each post is enough. NULL means never fetched,
    # which is a different fact from a fetched zero — hence nullable rather than
    # defaulting to 0.
    views = models.PositiveIntegerField(null=True, blank=True)
    reach = models.PositiveIntegerField(null=True, blank=True)
    likes = models.PositiveIntegerField(null=True, blank=True)
    comments_count = models.PositiveIntegerField(null=True, blank=True)
    saves = models.PositiveIntegerField(null=True, blank=True)
    shares = models.PositiveIntegerField(null=True, blank=True)
    total_interactions = models.PositiveIntegerField(null=True, blank=True)
    avg_watch_time_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Average watch time in milliseconds (reels only). The number "
        "that decides how far a reel travels.",
    )
    insights_fetched_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Posted {self.property_url} on {self.social_media} at {self.datetime}"

    @property
    def engagement_rate(self):
        """Interactions per person reached, as a percentage.

        None when either number is missing, so a post that has never been
        fetched is not reported as 0% engagement — which would read as a
        failure rather than as an absence of data.
        """
        if not self.reach or self.total_interactions is None:
            return None
        return 100 * self.total_interactions / self.reach


class SocialComment(models.Model):
    post = models.IntegerField()
    comment_id = models.IntegerField(unique=True)
    comment = models.CharField(max_length=200)
    replied = models.BooleanField(default=False)
    datetime = models.DateTimeField(auto_now_add=True)
    self_comment = models.BooleanField(default=False)

    def __str__(self):
        return f"Comment # {self.comment_id} - {self.post} at {self.datetime}"