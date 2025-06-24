from django.core.management.base import BaseCommand
from scrapper.scrapper import pull_properties
from scrapper.constants import LIFULL_HOMES_BASE_URL, LIFULL_HOMES_REGION_LIST

class Command(BaseCommand):
    help = 'Pull properties from the specified website'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--region',
            type=str,
            help='Specify the region to pull properties from',
            required=False
        )

    def handle(self, *args, **kwargs):
        try:
            region = kwargs.get('region')
            if region:
                page_from = 1
                page_to = 50
                listing_url = f"{LIFULL_HOMES_BASE_URL}/kodate/chuko/{region}/list/"
                pull_properties(listing_url=listing_url, page_from=page_from, page_to=page_to)
                self.stdout.write(self.style.SUCCESS(f'Successfully pulled properties - {region}'))
            else:
                for region in LIFULL_HOMES_REGION_LIST:
                    page_from = 1
                    page_to = 50
                    listing_url = f"{LIFULL_HOMES_BASE_URL}/kodate/chuko/{region}/list/"
                    pull_properties(listing_url=listing_url, page_from=page_from, page_to=page_to)
                    self.stdout.write(self.style.SUCCESS(f'Successfully pulled properties - {region}'))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Error pulling properties: {e}'))
