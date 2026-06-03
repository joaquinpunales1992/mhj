"""Backfill: rewrite broken SUUMO resizeImage URLs to direct CDN URLs.

The previous scraper persisted some images as img01.suumo.com/jj/resizeImage?...
URLs without the required &w=&h= query params, which 400s when the browser
fetches them. Each of those URLs embeds the canonical direct-CDN path in
its `src=` parameter (URL-encoded), so we can recover the working URL
without re-scraping.
"""
from __future__ import annotations

import re
import urllib.parse

from django.core.management.base import BaseCommand

from inventory.models import PropertyImage


_RESIZE_RE = re.compile(
    r"https?://img\d*\.suumo\.com/jj/resizeImage\?src=([^&\s]+)"
)


def _direct_from_resize(url: str) -> str | None:
    m = _RESIZE_RE.match(url)
    if not m:
        return None
    encoded_src = m.group(1)
    # src is URL-encoded (e.g. gazo%2Fbukken%2F...jpg). After decoding we get
    # a relative path like "gazo/bukken/...jpg" which lives at suumo.jp/front/.
    relative = urllib.parse.unquote(encoded_src)
    if not relative.startswith("gazo/"):
        return None
    return f"https://suumo.jp/front/{relative}"


class Command(BaseCommand):
    help = "Rewrite broken SUUMO resizeImage URLs in PropertyImage.file to direct CDN URLs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        qs = PropertyImage.objects.filter(file__contains="resizeImage")
        total = qs.count()
        self.stdout.write(f"Found {total} PropertyImage rows with resizeImage URLs.")

        updated = skipped = 0
        for img in qs.iterator():
            new_url = _direct_from_resize(img.file.name)
            if not new_url:
                skipped += 1
                continue
            if dry_run:
                if updated < 5:
                    self.stdout.write(f"  WOULD UPDATE id={img.id}: {img.file.name[:80]} -> {new_url[:80]}")
                updated += 1
                continue
            img.file = new_url
            img.save(update_fields=["file"])
            updated += 1
            if updated % 500 == 0:
                self.stdout.write(f"  ... {updated} updated")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {'(dry-run) ' if dry_run else ''}updated={updated} skipped={skipped}"
            )
        )
