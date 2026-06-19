import re

from django.core.management.base import BaseCommand

from inventory.models import PropertyImage

# Pull the encoded src out of a SUUMO resizeImage URL (everything up to '&').
_SRC_RE = re.compile(r"resizeImage\?src=([^&\s\"']+)")


class Command(BaseCommand):
    help = (
        "Normalize stored SUUMO resizeImage URLs to a full-width render "
        "(&w=1024, no fixed height). Fixes images saved as tiny thumbnails "
        "(&w=220&h=165) or white-padded fixed boxes (&w=1024&h=768)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many would change without writing.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        qs = PropertyImage.objects.filter(file__contains="resizeImage?src=")

        changed = 0
        for image in qs.iterator():
            current = str(image.file)
            match = _SRC_RE.search(current)
            if not match:
                continue
            new_url = (
                f"https://img01.suumo.com/jj/resizeImage?src={match.group(1)}&w=1024"
            )
            if new_url == current:
                continue
            changed += 1
            if not dry_run:
                image.file = new_url
                image.save(update_fields=["file"])

        verb = "Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{verb} {changed} image(s)."))
