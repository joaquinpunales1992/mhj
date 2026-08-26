"""Replace stored Google error pages with real text.

Some rows carry Google's own error page where a translated field should be:

    Error 500 (Server Error)!!1500.That's an error.There was an error.
    Please try again later.That's all we know.

The translation endpoint answers 500 with a page rather than a status the
client library raises on, so deep-translator hands back the page text as if it
were a translation and the scraper saves it. `safe_translate` now rejects that
and keeps the Japanese instead, so no new ones appear — this is for the rows
written before that.

It shows up worse than its row count suggests: the poisoned field is usually
`title`, get_title_for_front cuts at 20 characters, and a card ends up reading
"Error 500 (Server Er...".

Where the listing is still up and we have a parser for it, the fields are
re-fetched and re-translated. Where it is not — a delisted listing, or one of
the homes.co.jp rows from a scraper that no longer exists — the field is
cleared, because an empty title is a smaller lie than Google's apology.

Only the poisoned fields are touched. Everything else on the row, including
anything curated by hand, is left exactly as it was.

Safe to re-run and safe to interrupt: each property is saved as decided.

    manage.py repair_translations --dry-run     # report only
    manage.py repair_translations               # apply
    manage.py repair_translations --limit 20    # work through a few first
"""

import logging
import time

from django.core.management.base import BaseCommand
from django.db.models import Q

from inventory.models import Property

logger = logging.getLogger(__name__)

# Be polite to the source sites; this only ever walks the damaged rows.
REQUEST_INTERVAL_SECONDS = 1.5

# Which scraper module can re-read a URL, by host. Rows from any other host
# (homes.co.jp, scraped before these modules existed) can only be cleared.
SOURCE_BY_HOST = {
    "suumo.jp": "suumo",
    "www.suumo.jp": "suumo",
    "athome.co.jp": "athome",
    "www.athome.co.jp": "athome",
}

# Model field <- key in the dict parse_listing returns. Only the text fields a
# translation can poison; price, dates and images are parsed from the raw
# Japanese and are never affected.
FIELD_SOURCE_KEYS = {
    "title": "property_title",
    "location": "location",
    "traffic": "traffic",
    "description": "remarks",
    "floor_plan": "floor_plan",
    "building_area": "building_area",
    "land_area": "land_area",
    "construction_date": "building_age",
    "building_structure": "building_structure",
    "road_condition": "road_condition",
    "city_planning": "city_planning",
    "zoning": "zoning",
    "land_category": "land_category",
    "building_coverage_ratio": "building_coverage_ratio",
    "floor_area_ratio": "floor_area_ratio",
    "handover": "handover",
    "equipment": "equipment",
    "transaction_type": "transaction_type",
    "land_rights": "land_rights",
    "renovation": "renovation",
    "estimated_utility_cost": "estimated_utility_cost",
    "insulation_performance": "insulation_performance",
    "energy_performance": "energy_performance",
}


def poisoned_properties():
    """Every property with an error page stored in a text field."""
    from scrapper.scrapper import TRANSLATION_ERROR_MARKERS

    query = Q()
    for field in FIELD_SOURCE_KEYS:
        for marker in TRANSLATION_ERROR_MARKERS:
            query |= Q(**{f"{field}__contains": marker})
    return Property.objects.filter(query).distinct()


def poisoned_fields(prop):
    """Which of this row's fields hold an error page."""
    from scrapper.scrapper import looks_like_an_error_page

    return [
        field for field in FIELD_SOURCE_KEYS
        if looks_like_an_error_page(getattr(prop, field, "") or "")
    ]


class Command(BaseCommand):
    help = "Replace stored translation error pages with real text, or clear them."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Report without writing anything.")
        parser.add_argument("--limit", type=int, default=0,
                            help="Only repair this many (0 = all).")

    def handle(self, *args, **options):
        # Imported here so the command still loads on a host that never
        # scrapes and lacks the optional dependencies.
        from scrapper.sources import SOURCES

        dry_run = options["dry_run"]
        suspects = list(poisoned_properties().order_by("pk"))
        if options["limit"]:
            suspects = suspects[: options["limit"]]

        total = len(suspects)
        self.stdout.write(
            f"{total} propert{'y' if total == 1 else 'ies'} with a stored "
            f"translation error{' (dry run)' if dry_run else ''}."
        )
        if not total:
            return

        refetched = cleared = failed = 0

        for i, prop in enumerate(suspects, 1):
            fields = poisoned_fields(prop)
            host = prop.url.split("/")[2] if "//" in prop.url else ""
            module_name = SOURCE_BY_HOST.get(host)
            label = f"[{i}/{total}] #{prop.pk} {host} {', '.join(fields)}"

            data = None
            if module_name and module_name in SOURCES:
                if i > 1:
                    time.sleep(REQUEST_INTERVAL_SECONDS)
                try:
                    data = SOURCES[module_name].parse_listing(url=prop.url)
                except Exception as exc:
                    logger.warning("Could not re-read %s: %s", prop.url, exc)

            fixed_here = []
            for field in fields:
                fresh = (data or {}).get(FIELD_SOURCE_KEYS[field], "") if data else ""
                # A re-read that returns another error page, or nothing, is not
                # an improvement — clear the field rather than store it again.
                from scrapper.scrapper import looks_like_an_error_page
                if fresh and not looks_like_an_error_page(fresh):
                    setattr(prop, field, fresh)
                    fixed_here.append(field)
                else:
                    setattr(prop, field, "")

            if fixed_here:
                refetched += 1
                self.stdout.write(f"{label} -> re-read {', '.join(fixed_here)}")
            elif data is None and module_name:
                failed += 1
                cleared += 1
                self.stdout.write(f"{label} -> listing unreachable, cleared")
            else:
                cleared += 1
                reason = "no parser for this host" if not module_name else "no fresh text"
                self.stdout.write(f"{label} -> {reason}, cleared")

            if not dry_run:
                prop.save(update_fields=fields)

        self.stdout.write(
            f"\n{refetched} re-read, {cleared} cleared"
            + (f" ({failed} unreachable)" if failed else "")
            + (" — dry run, nothing written." if dry_run else ".")
        )
