"""Re-read prices for properties whose stored price is implausibly low.

Some rows carry a price around 100x too small — a Tokyo house stored as 17万
(~$1,190) whose listing actually says 1760万 (~$123,000). They come from an
older scrape that read the *translated* price string: Google renders "1760万円"
as "17.6 million yen", and taking the leading integer yields 17. The current
parser (scrapper.parse_jpy_price) reads the raw Japanese before translation and
gets these right — verified against the live listings — so this is bad stored
data, not a live bug.

It matters out of proportion to the row count: the map's list panel and any
cheapest-first sort put these rows at the very top, so a handful of broken
prices are the first thing a visitor sees.

Re-fetches each suspect listing and either corrects the price or, when the
listing has 404'd, leaves it for `retire_delisted_properties` and reports it.

Safe to re-run and safe to interrupt: each property is committed as decided.

    manage.py repair_prices --dry-run          # report only
    manage.py repair_prices                    # apply
    manage.py repair_prices --below 50         # change the suspicion threshold
"""

import logging
import time

from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand

from inventory.models import Property

logger = logging.getLogger(__name__)

# A detached house below this (in 万) is not a real asking price — the cheapest
# genuine akiya in the table sit in the low hundreds of 万. Deliberately
# conservative: it is far better to skip a real bargain than to rewrite a
# correct price.
DEFAULT_SUSPECT_BELOW_MAN = 50

# Be polite to the source sites; this only ever walks a handful of rows.
REQUEST_INTERVAL_SECONDS = 1.5

# Refuse a "correction" that moves the price by more than this factor. A wild
# swing means the URL now points at a different listing (SUUMO recycles them),
# not that we found the true price.
MAX_CORRECTION_FACTOR = 1000


class Command(BaseCommand):
    help = "Re-fetch and correct implausibly low property prices."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Report without writing anything.")
        parser.add_argument("--below", type=int, default=DEFAULT_SUSPECT_BELOW_MAN,
                            help=f"Treat prices under this many 万 as suspect "
                                 f"(default {DEFAULT_SUSPECT_BELOW_MAN}).")
        parser.add_argument("--limit", type=int, default=0,
                            help="Only check this many (0 = all).")

    def handle(self, *args, **options):
        # Imported here so the module still loads if the scraper's optional
        # dependencies are missing on a host that never scrapes.
        from scrapper.scrapper import fetch, parse_jpy_price

        dry_run = options["dry_run"]
        threshold = options["below"]

        suspects = Property.objects.filter(
            price__gt=0, price__lt=threshold
        ).order_by("price")
        if options["limit"]:
            suspects = suspects[: options["limit"]]

        total = suspects.count() if hasattr(suspects, "count") else len(suspects)
        self.stdout.write(
            f"{total} propert{'y' if total == 1 else 'ies'} priced under "
            f"{threshold}万{' (dry run)' if dry_run else ''}."
        )
        if not total:
            return

        fixed = gone = unchanged = skipped = 0

        for i, prop in enumerate(suspects, 1):
            if i > 1:
                time.sleep(REQUEST_INTERVAL_SECONDS)

            label = f"[{i}/{total}] #{prop.pk} stored={prop.price}万"

            try:
                response = fetch(prop.url)
            except Exception as e:
                self.stderr.write(f"  {label} fetch error: {e}")
                skipped += 1
                continue

            if not response:
                # Delisted. Retiring is retire_delisted_properties' job — it
                # checks the images too — so just surface it here.
                gone += 1
                self.stdout.write(self.style.WARNING(
                    f"  {label} -> source gone (404). Run "
                    f"retire_delisted_properties."
                ))
                continue

            raw = self._raw_price(response.text, prop.url)
            yen = parse_jpy_price(raw) if raw else None
            man = (yen or 0) // 10_000

            if not man:
                self.stdout.write(f"  {label} -> no price found on page, skipped")
                skipped += 1
                continue

            if man == prop.price:
                unchanged += 1
                self.stdout.write(f"  {label} -> confirmed, price is correct")
                continue

            factor = man / prop.price if prop.price else 0
            if factor > MAX_CORRECTION_FACTOR:
                skipped += 1
                self.stderr.write(self.style.WARNING(
                    f"  {label} -> live={man}万 is {factor:.0f}x higher; "
                    f"refusing (URL likely relisted). Check by hand."
                ))
                continue

            self.stdout.write(self.style.SUCCESS(
                f"  {label} -> {man}万 (x{factor:.0f}) {raw[:24]!r}"
            ))
            if not dry_run:
                prop.price = man
                prop.save(update_fields=["price"])
            fixed += 1

        summary = (f"Done. {fixed} corrected, {unchanged} already right, "
                   f"{gone} delisted, {skipped} skipped.")
        self.stdout.write(self.style.SUCCESS(
            summary + (" (dry run — nothing written)" if dry_run else "")
        ))

    @staticmethod
    def _raw_price(html, url):
        """Pull the raw Japanese 価格 value, picking the parser by source host."""
        soup = BeautifulSoup(html, "html.parser")
        if "athome" in url:
            from scrapper.sources.athome import _extract_table_data
        else:
            from scrapper.sources.suumo import _extract_table_data
        return _extract_table_data(soup).get("価格", "")
