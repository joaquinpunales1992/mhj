"""Fill in the station fields from each property's scraped `traffic` text.

    manage.py parse_transit                 properties not parsed yet
    manage.py parse_transit --all           re-parse everything
    manage.py parse_transit --dry-run       report coverage, write nothing

Pure derivation — no network, no API limits — so it is cheap to re-run after any
change to the parser. New listings get these fields at scrape time; this command
exists to backfill the ones already in the table, and to re-derive them all when
the parser learns a new phrasing.

Reports how many listings name a walkable station, because that number is the
point: it is the size of the audience for a "within N minutes of a station"
filter, and it is not something to guess at.
"""

from django.core.management.base import BaseCommand
from django.db import models

from inventory.models import Property
from inventory.utils import parse_transit


class Command(BaseCommand):
    help = "Derive nearest station and walk time from the scraped traffic text."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true",
                            help="Re-parse every property, not just unparsed ones.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would change without saving.")
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **options):
        rows = Property.objects.exclude(traffic="")
        if not options["all"]:
            # Unparsed means: no station recorded and not yet marked bus-only.
            rows = rows.filter(nearest_station="", needs_bus=False)
        if options["limit"]:
            rows = rows[: options["limit"]]

        walk = km = bus_only = nothing = 0
        changed = 0
        buckets = {"≤5 min": 0, "6-10 min": 0, "11-20 min": 0, "21+ min": 0}

        for property in rows.iterator(chunk_size=500):
            transit = parse_transit(property.traffic)
            if transit["walk_minutes"] is not None:
                walk += 1
                minutes = transit["walk_minutes"]
                key = ("≤5 min" if minutes <= 5 else "6-10 min" if minutes <= 10
                       else "11-20 min" if minutes <= 20 else "21+ min")
                buckets[key] += 1
            elif transit["distance_km"] is not None:
                km += 1
            elif transit["needs_bus"]:
                bus_only += 1
            else:
                nothing += 1

            if options["dry_run"]:
                continue

            property.nearest_station = transit["station"]
            property.station_walk_minutes = transit["walk_minutes"]
            property.station_distance_km = transit["distance_km"]
            property.needs_bus = transit["needs_bus"]
            property.save(update_fields=[
                "nearest_station", "station_walk_minutes", "station_distance_km",
                "needs_bus",
            ])
            changed += 1

        total = walk + km + bus_only + nothing
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nTransit parsed — {total} listings with a traffic description"
        ))
        self._row("walkable station", walk, total)
        for label, count in buckets.items():
            if count:
                self.stdout.write(f"      {label:<12} {count}")
        self._row("distance only (km)", km, total)
        self._row("bus access only", bus_only, total)
        self._row("unrecognised", nothing, total)

        if nothing:
            self.stdout.write(
                "\n  Unrecognised means the parser found neither a station nor a\n"
                "  bus. Worth a look if that number grows — it is the signal that\n"
                "  the source site has changed its phrasing."
            )

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("\n  Dry run — nothing saved.\n"))
        else:
            self.stdout.write(self.style.SUCCESS(f"\n  Updated {changed} listings.\n"))

        # Whole-table picture, so a partial run doesn't read as the full state.
        located = Property.objects.filter(
            station_walk_minutes__isnull=False
        ).count()
        near = Property.objects.filter(station_walk_minutes__lte=15).count()
        self.stdout.write(
            f"  Table total: {located} listings have a walk time, "
            f"{near} within 15 minutes.\n"
        )

    def _row(self, label, value, of=None):
        share = f"  ({100 * value // of}%)" if of else ""
        self.stdout.write(f"    {label:<22} {value}{share}")
