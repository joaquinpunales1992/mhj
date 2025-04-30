from django.core.management.base import BaseCommand
from scrapper.scrapper import pull_properties

class Command(BaseCommand):
    help = 'Pull properties from the specified website'

    def handle(self, *args, **kwargs):
        try:
            page_limit = 15
            url = "https://www.homes.co.jp/kodate/chuko/miyazaki/list/"
            pull_properties(url=url, page_limit=page_limit)
            self.stdout.write(self.style.SUCCESS('Successfully pulled properties.'))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Error pulling properties: {e}'))
