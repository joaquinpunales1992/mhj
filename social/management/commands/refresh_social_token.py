from django.core.management.base import BaseCommand
from social.social_bot import refresh_access_token

class Command(BaseCommand):
    help = 'Refresh Access Token'

    def handle(self, *args, **kwargs):
        try:
            refresh_access_token()
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Error while Refreshing TOKEN: {e}'))
