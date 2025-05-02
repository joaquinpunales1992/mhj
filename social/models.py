from django.db import models

class SocialPost(models.Model):
    """
    A model representing a social media post.
    """
    caption = models.TextField()
    datetime = models.DateTimeField(auto_now_add=True)
    property_url = models.URLField(max_length=255, blank=True)
    social_media = models.CharField(max_length=50, choices=[('facebook', 'Facebook'), ('instagram', 'Instagram')])

    def __str__(self):
        return f"Posted {self.property_url} on {self.social_media} at {self.datetime}"