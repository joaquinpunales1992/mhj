from django.core.management.base import BaseCommand
from social.utils import post_facebook_reel
from dotenv import load_dotenv

load_dotenv("/home/planlxry/myhouseinjapan/.env")


class Command(BaseCommand):
    help = "Post on Instagram Reel"

    def handle(self, *args, **kwargs):
        post_facebook_reel()
