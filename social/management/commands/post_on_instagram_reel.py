import logging

from django.core.management.base import BaseCommand
from social.social_bot import post_on_instagram_batch
from social.utils import post_instagram_reel
from membership.utils import notify_social_token_expired
from social.constants import PRICE_LIMIT_INSTAGRAM, BATCH_SIZE_INSTAGRAM
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
load_dotenv()  # load .env from current working dir


class Command(BaseCommand):
    help = "Post on Instagram Reel"

    def handle(self, *args, **kwargs):
        post_instagram_reel()
