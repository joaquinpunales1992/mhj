from django.core.management.base import BaseCommand
from social.social_bot import post_on_instagram_batch
from social.utils import post_instagram_reel
from membership.utils import notify_social_token_expired
from social.constants import PRICE_LIMIT_INSTAGRAM, BATCH_SIZE_INSTAGRAM

class Command(BaseCommand):
    help = 'Post on Instagram Reel'

    def handle(self, *args, **kwargs):
        try:
            post_instagram_reel()
        except Exception as e:
            notify_social_token_expired()
            self.stderr.write(self.style.ERROR(f'Error on Instagram Reel: {e}'))
