from django.core.management.base import BaseCommand
from social.social_bot import post_on_instagram_batch, reply_comments_instagram
from social.utils import post_instagram_reel
from membership.utils import notify_social_token_expired
from social.constants import PRICE_LIMIT_INSTAGRAM, BATCH_SIZE_INSTAGRAM
from dotenv import load_dotenv

load_dotenv("/home/planlxry/myhouseinjapan/.env")


class Command(BaseCommand):
    help = "Reply commnents Instagram"

    def handle(self, *args, **kwargs):
        reply_comments_instagram()



