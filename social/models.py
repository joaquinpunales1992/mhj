from django.db import models


class SocialPost(models.Model):
    """
    A model representing a social media post.
    """

    # What editorial format this was, as opposed to `content_type` below which
    # is the *media* shape (still image vs video). The two are independent: an
    # FAQ can go out as a carousel or as a reel. Stored so insights.group_by can
    # answer "which format earns attention", which is the whole point of
    # posting anything other than listings.
    KIND_LISTING = "listing"
    KIND_FAQ = "faq"
    KIND_NEWS = "news"
    KIND_DATA = "data"
    KIND_GUIDE = "guide"
    KIND_CHOICES = [
        (KIND_LISTING, "Listing"),
        (KIND_FAQ, "FAQ"),
        (KIND_NEWS, "News"),
        (KIND_DATA, "Data / stats"),
        (KIND_GUIDE, "Guide"),
    ]
    post_kind = models.CharField(
        max_length=32,
        choices=KIND_CHOICES,
        default=KIND_LISTING,
        db_index=True,
        help_text="Editorial format. Everything posted before this field "
        "existed was a listing, which is why that is the default.",
    )

    ai_caption = models.TextField(default="", blank=True)
    caption = models.TextField()
    datetime = models.DateTimeField(auto_now_add=True)
    property_url = models.URLField(max_length=255, blank=True)
    social_media = models.CharField(
        max_length=50,
        choices=[
            ("facebook", "Facebook"),
            ("instagram", "Instagram"),
            ("tiktok", "TikTok"),
        ],
    )
    content_type = models.CharField(
        max_length=50,
        choices=[("post", "Post"), ("reel", "Reel"), ("story", "Story")],
        default="post",
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
    # `comment` is what WE replied. The follower's own words used to be thrown
    # away after the reply was generated, which meant the single best source of
    # FAQ material — the questions people actually ask us — was fetched and
    # discarded on every run. Kept now, so the FAQ bank can eventually be
    # driven by real questions instead of a hand-written list.
    question = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="The follower's comment we were replying to. Blank on rows "
        "written before this field existed.",
    )
    comment = models.CharField(max_length=200)
    replied = models.BooleanField(default=False)
    datetime = models.DateTimeField(auto_now_add=True)
    self_comment = models.BooleanField(default=False)

    def __str__(self):
        return f"Comment # {self.comment_id} - {self.post} at {self.datetime}"

class ContentDraft(models.Model):
    """A piece of non-listing content, held for a human's approval.

    Listings and stats posts are derived from facts we own, so they can post
    themselves. This model exists for the content where being confidently wrong
    is expensive: an FAQ answer about visas, taxes or what a foreign buyer is
    allowed to do. Nothing here reaches an audience until someone approves it
    in the admin, and the copy is generated from a fixed set of facts rather
    than from the model's own knowledge (see social/content/faq_bank.py).
    """

    STATUS_DRAFT = "draft"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_POSTED = "posted"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "1. Draft — needs review"),
        (STATUS_APPROVED, "2. Approved — will post"),
        (STATUS_REJECTED, "✕ Rejected"),
        (STATUS_POSTED, "3. Posted"),
    ]

    SOURCE_BANK = "bank"
    SOURCE_COMMENT = "comment"
    SOURCE_CHOICES = [
        (SOURCE_BANK, "Curated question bank"),
        (SOURCE_COMMENT, "Asked by a follower"),
    ]

    kind = models.CharField(
        max_length=32,
        choices=SocialPost.KIND_CHOICES,
        default=SocialPost.KIND_FAQ,
        db_index=True,
    )
    status = models.CharField(
        max_length=32, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True
    )
    source = models.CharField(
        max_length=32, choices=SOURCE_CHOICES, default=SOURCE_BANK
    )

    # `key` identifies the subject, not the draft: it is what stops the same
    # question being asked again next week. Not unique — a question may be
    # legitimately reposted months later — but it is what the cooldown reads.
    key = models.CharField(max_length=120, db_index=True)
    question = models.CharField(max_length=500)
    answer = models.TextField(
        help_text="The answer as it appears on the card. Edit it here before "
        "approving; the card is re-rendered on approval."
    )
    caption = models.TextField(
        blank=True, default="", help_text="Caption as it will be posted."
    )
    card_paths = models.TextField(
        blank=True,
        default="",
        help_text="Newline-separated local paths of the rendered cards, in "
        "carousel order.",
    )

    # Set by the bank when an answer contains a number, a legal rule or
    # anything else that goes stale. Surfaced in the admin so these get read
    # properly rather than waved through.
    needs_review = models.BooleanField(
        default=False,
        help_text="This answer states something specific enough to be wrong. "
        "Check it before approving.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.get_status_display()}] {self.kind}: {self.question[:60]}"

    @property
    def card_path_list(self):
        return [p for p in self.card_paths.splitlines() if p.strip()]
