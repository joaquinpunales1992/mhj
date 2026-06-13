from django.core.management.base import BaseCommand
from social.utils import post_facebook_reel
from dotenv import load_dotenv

load_dotenv()  # load .env from current working dir


class Command(BaseCommand):
    help = "Post on Facebook Reel"

    def handle(self, *args, **kwargs):
        post_facebook_reel()
