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

Where the listing is still up and we have a parser for it, the poisoned field
is re-fetched and re-translated. Where there is no parser for the host at all —
the homes.co.jp rows, from a scraper that no longer exists — the field is
cleared, because an empty title is a smaller lie than Google's apology.

A listing we *could* read but could not reach is left alone, not cleared.
parse_listing returns None for a 404, a block and a dropped connection alike,
so clearing on that would blank every title the moment a source site starts
rate-limiting the scraper.

ONLY THE POISONED FIELD IS TRANSLATED. The first version re-parsed the whole
listing, which translates about twenty-three fields, and ran that across every
damaged row. On a real run of 193 properties that was several thousand calls in
a burst; the translator started answering with error pages, and the command
began writing raw Japanese into the very rows it was there to fix — a row that
holds Japanese no longer matches the error markers, so it could not be found
and repaired afterwards either. The repair was reproducing the bug it cleans up.

So: the listing is parsed with translate=False, and exactly the broken field is
translated, one call. The translator is tested before any of it starts, and the
run aborts if it is failing rather than grinding through the rows doing damage.
Nothing is written for a row whose translation did not come back in English.

Only the poisoned fields are touched. Everything else on the row, including
anything curated by hand, is left exactly as it was.

Safe to re-run and safe to interrupt: each property is saved as decided.

    manage.py repair_translations --dry-run     # report only
    manage.py repair_translations               # apply
    manage.py repair_translations --limit 20    # work through a few first
    manage.py repair_translations --find-japanese   # rows an earlier run damaged
"""

import logging
import re
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


# A phrase whose English is not itself: if this comes back unchanged, the
# translator is not translating.
CANARY = "中古一戸建て"

# Give up after this many consecutive fields come back untranslated. The
# translator rate-limits rather than refusing outright, so the first failure may
# be a blip and the third is a pattern.
MAX_CONSECUTIVE_FAILURES = 3

# Japanese, for spotting text that never got translated. Hiragana, katakana and
# the CJK ideographs.
JAPANESE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")


def translator_is_working():
    """One call, before doing anything that writes."""
    from scrapper.scrapper import safe_translate

    try:
        answer = safe_translate(CANARY)
    except Exception:
        return False
    # safe_translate hands back the original when it fails, so an unchanged
    # answer is a failed one.
    return bool(answer) and answer != CANARY


# Above this share of Japanese characters, a field was never translated at all.
# Below it, the odd kanji in otherwise English text — a station name, a 坪 —
# which is ordinary and not damage. Measured against real rows: an untranslated
# title runs 0.5 upwards, a translated one with a stray character under 0.1.
JAPANESE_SHARE_UNTRANSLATED = 0.3


def japanese_share(text):
    """How much of this is Japanese, ignoring spaces."""
    body = "".join((text or "").split())
    if not body:
        return 0.0
    return len(JAPANESE.findall(body)) / len(body)


def looks_untranslated(text):
    """True when a 'translation' came back as Japanese.

    A share rather than "contains any Japanese": a correctly translated address
    can still carry a kanji, and treating that as a failure would refuse good
    translations and leave rows broken.
    """
    return japanese_share(text) > JAPANESE_SHARE_UNTRANSLATED


def japanese_properties():
    """Rows holding Japanese where English belongs.

    For assessing what an interrupted run left behind: it wrote the untranslated
    original into these, which means they no longer match the error markers and
    poisoned_properties() cannot see them.

    The database narrows it to rows containing any Japanese; the share test is
    applied in Python, since it is not something a query can express.
    """
    query = Q()
    for field in ("title", "location"):
        query |= Q(**{f"{field}__regex": r"[\u3040-\u30ff\u4e00-\u9fff]"})
    return [
        prop for prop in Property.objects.filter(query).distinct()
        if any(looks_untranslated(getattr(prop, f, ""))
               for f in ("title", "location"))
    ]


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
        parser.add_argument("--find-japanese", action="store_true",
                            help="List rows holding untranslated Japanese, which "
                                 "is what an earlier run left behind. Reports "
                                 "only; never writes.")

    def handle(self, *args, **options):
        # Imported here so the command still loads on a host that never
        # scrapes and lacks the optional dependencies.
        from scrapper.scrapper import looks_like_an_error_page, safe_translate
        from scrapper.sources import SOURCES

        if options["find_japanese"]:
            return self.report_japanese()

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

        # Checked once, and only when a translation is actually about to
        # happen. A run against a failing translator does damage rather than
        # nothing — it stores the untranslated Japanese, and the row stops
        # matching the markers that would let us find it again — but clearing a
        # row we have no parser for needs no translator at all, and should not
        # be blocked by one being down.
        checked = {}

        def translator_ok():
            if "ok" not in checked:
                checked["ok"] = translator_is_working()
                if not checked["ok"]:
                    self.stderr.write(self.style.ERROR(
                        "The translator is not translating right now — it is "
                        "returning error pages or rate-limiting.\n"
                        "Stopping before anything is rewritten. Try again "
                        "later; the rows are still findable, and that is worth "
                        "keeping."
                    ))
            return checked["ok"]

        refetched = cleared = failed = 0
        consecutive_failures = 0

        for i, prop in enumerate(suspects, 1):
            fields = poisoned_fields(prop)
            host = prop.url.split("/")[2] if "//" in prop.url else ""
            module_name = SOURCE_BY_HOST.get(host)
            label = f"[{i}/{total}] #{prop.pk} {host} {', '.join(fields)}"

            raw = None
            if module_name and module_name in SOURCES:
                if i > 1:
                    time.sleep(REQUEST_INTERVAL_SECONDS)
                try:
                    # translate=False: the raw Japanese, one HTTP fetch, no
                    # translation calls. Only the broken fields get translated,
                    # below — re-translating all twenty-three is what caused
                    # the rate-limiting this command exists to clean up after.
                    raw = SOURCES[module_name].parse_listing(
                        url=prop.url, translate=False
                    )
                except Exception as exc:
                    logger.warning("Could not re-read %s: %s", prop.url, exc)

            # A parser exists but the re-read came back empty. That is a 404 and
            # a blocked request and a dropped connection, and parse_listing
            # returns None for all three — so clearing here would blank every
            # title the moment the source site rate-limits us. Left alone: the
            # error text is ugly and recoverable, a cleared title is neither.
            if module_name and raw is None:
                failed += 1
                self.stdout.write(f"{label} -> could not re-read, left alone")
                continue

            repaired, blanked, gave_up = [], [], False
            for field in fields:
                source = (raw or {}).get(FIELD_SOURCE_KEYS[field], "") if raw else ""
                if not source:
                    blanked.append(field)
                    continue

                if not dry_run and not translator_ok():
                    return
                fresh = safe_translate(source)
                if looks_like_an_error_page(fresh) or looks_untranslated(fresh):
                    # Storing this would write Japanese into the row and make it
                    # invisible to a later run. Leave the error text in place; it
                    # is ugly and it is findable.
                    consecutive_failures += 1
                    gave_up = True
                    break
                consecutive_failures = 0
                repaired.append((field, fresh))

            if gave_up:
                failed += 1
                self.stdout.write(f"{label} -> translation failed, left alone")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    self.stderr.write(self.style.ERROR(
                        f"\nStopped after {consecutive_failures} failures in a "
                        f"row: the translator is rate-limiting.\n"
                        f"{refetched} repaired, {cleared} cleared before that. "
                        f"Re-run later — the rest are untouched and still findable."
                    ))
                    return
                continue

            for field, value in repaired:
                setattr(prop, field, value)
            for field in blanked:
                setattr(prop, field, "")

            if repaired:
                refetched += 1
                self.stdout.write(
                    f"{label} -> re-read {', '.join(f for f, _ in repaired)}")
            if blanked:
                cleared += 1
                reason = "no parser for this host" if not module_name else "no fresh text"
                self.stdout.write(f"{label} -> {reason}, cleared")

            if not dry_run:
                prop.save(update_fields=fields)

        self.stdout.write(
            f"\n{refetched} re-read, {cleared} cleared"
            + (f", {failed} left alone (translation failed)" if failed else "")
            + (" — dry run, nothing written." if dry_run else ".")
        )

    def report_japanese(self):
        """What an earlier run wrote when the translator was down."""
        rows = sorted(japanese_properties(), key=lambda p: p.pk)
        total = len(rows)
        self.stdout.write(
            f"{total} propert{'y' if total == 1 else 'ies'} hold untranslated "
            f"Japanese in title or location."
        )
        if not total:
            return
        for prop in rows[:40]:
            for field in ("title", "location"):
                value = getattr(prop, field, "")
                if looks_untranslated(value):
                    self.stdout.write(
                        f"  #{prop.pk} {field} "
                        f"({japanese_share(value):.0%} Japanese): {value[:60]}")
        if total > 40:
            self.stdout.write(f"  ... and {total - 40} more")
        self.stdout.write(
            "\nThese no longer match the error markers, so the repair cannot "
            "find them.\nRe-scraping the listing is the only way back to English."
        )
