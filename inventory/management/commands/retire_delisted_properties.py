"""Hide properties whose source listing (SUUMO / homes.co.jp) has been removed.

Listings expire, but nothing ever cleared them from our side, so dead
properties stayed `show_in_front=True` forever. On the site they render as
black cards — the photo 404s, and the detail page behind "Learn More" is a
wall of broken images. They also pile up at the head of the social posting
queue, which orders never-posted properties first.

The reel posters retire a delisted property when they happen to reach one
(see social.utils.create_property_video), but that only ever touches the few
candidates at the front of the queue. This command sweeps the whole table.

Safe to re-run, and safe to interrupt: each property is committed as it is
decided, so a killed run keeps its progress.

    manage.py retire_delisted_properties --dry-run     # report only
    manage.py retire_delisted_properties              # apply
"""

import logging

from django.core.management.base import BaseCommand

from inventory.models import Property, PropertyImage
from inventory.utils import all_images_gone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Set show_in_front=False on properties whose source photos all 404."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be retired without writing anything.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Only check this many properties (0 = all). Useful on the VPS "
            "to keep a single run short.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]

        queryset = Property.objects.filter(show_in_front=True).order_by("pk")
        total = queryset.count()
        if limit:
            queryset = queryset[:limit]

        self.stdout.write(
            f"Checking {limit or total} of {total} visible properties"
            f"{' (dry run)' if dry_run else ''}..."
        )

        checked = retired = skipped_no_photos = 0

        for prop in queryset.iterator():
            checked += 1

            # str(image.file) is the URL the browser actually requests, so it
            # is what decides whether a visitor sees a photo or a black card.
            # Anything not remotely hosted can't be "delisted" — skip it.
            photo_urls = [
                str(image.file)
                for image in PropertyImage.objects.filter(property_id=prop.pk)
                if str(image.file).startswith("http")
            ]

            # No remote photos is not evidence the listing is gone.
            if not photo_urls:
                skipped_no_photos += 1
                continue

            if not all_images_gone(photo_urls):
                continue

            retired += 1
            self.stdout.write(
                self.style.WARNING(
                    f"  delisted: pk={prop.pk} {prop.url[:80]} "
                    f"({len(photo_urls)} photos, all gone)"
                )
            )
            if not dry_run:
                Property.objects.filter(pk=prop.pk).update(show_in_front=False)

            if checked % 100 == 0:
                self.stdout.write(f"  ...{checked} checked, {retired} retired")

        verb = "would retire" if dry_run else "retired"
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: checked {checked}, {verb} {retired}, "
                f"skipped {skipped_no_photos} with no remote photos."
            )
        )
        if retired and not dry_run:
            self.stdout.write(
                "The home page is cached — clear the cache or wait for it to "
                "expire before the black cards disappear."
            )
