from django.core.management.base import BaseCommand
from social.social_bot import post_on_facebook_batch
from social.constants import PRICE_LIMIT_FACEBOOK, BATCH_SIZE_FACEBOOK

class Command(BaseCommand):
    help = 'Post on Facebook'

    def handle(self, *args, **kwargs):
        try:
            post_on_facebook_batch(price_limit=PRICE_LIMIT_FACEBOOK, batch_size=BATCH_SIZE_FACEBOOK)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Error on Facebook Post: {e}'))
