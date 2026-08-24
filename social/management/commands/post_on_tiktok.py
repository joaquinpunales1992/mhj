from django.core.management.base import BaseCommand

from membership.utils import notify_social_token_expired
from social.utils import post_tiktok_reel


class Command(BaseCommand):
    help = "Post the next queued property to TikTok"

    def handle(self, *args, **kwargs):
        try:
            if post_tiktok_reel():
                self.stdout.write(self.style.SUCCESS("Posted to TikTok."))
            else:
                self.stdout.write("Nothing was posted; see the log.")
        except Exception as e:
            notify_social_token_expired()
            self.stderr.write(self.style.ERROR(f"Error on TikTok post: {e}"))
