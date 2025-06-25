from django.core.management.base import BaseCommand
from social.social_bot import post_on_instagram_batch
from membership.utils import notify_social_token_expired
from social.constants import PRICE_LIMIT_INSTAGRAM, BATCH_SIZE_INSTAGRAM

class Command(BaseCommand):
    help = 'Post on Instagram'

    def handle(self, *args, **kwargs):
        try:
            post_on_instagram_batch(price_limit=PRICE_LIMIT_INSTAGRAM, batch_size=BATCH_SIZE_INSTAGRAM)
        except Exception as e:
            notify_social_token_expired()
            self.stderr.write(self.style.ERROR(f'Error on Instagram Post: {e}'))
