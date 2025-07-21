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

    def __str__(self):
        return f"Posted {self.property_url} on {self.social_media} at {self.datetime}"


class SocialComment(models.Model):
    post = models.IntegerField()
    comment_id = models.IntegerField(unique=True)
    comment = models.CharField(max_length=200)
    replied = models.BooleanField(default=False)
    datetime = models.DateTimeField(auto_now_add=True)
    self_comment = models.BooleanField(default=False)

    def __str__(self):
        return f"Comment # {self.comment_id} - {self.post} at {self.datetime}"