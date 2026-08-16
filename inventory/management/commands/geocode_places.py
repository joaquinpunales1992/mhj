"""Resolve 'City, Prefecture' keys to coordinates for the map view.

Runs against the distinct city keys of live properties rather than the
properties themselves: ~10k listings collapse to a few hundred cities, which
keeps the whole job inside Nominatim's fair-use policy and finishes in minutes
instead of hours.

Usage:
    manage.py geocode_places              # geocode keys we don't have yet
    manage.py geocode_places --limit 20   # try a small batch first
    manage.py geocode_places --retry      # re-attempt keys that failed before
    manage.py geocode_places --dry-run    # list what would be looked up
"""

import re
import time

import requests
from django.core.management.base import BaseCommand
from django.utils import timezone

from inventory.models import GeocodedPlace, Property
from inventory.utils import city_key

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Nominatim's usage policy requires an identifying User-Agent and at most one
# request per second. Both are conditions of the free service, not politeness.
USER_AGENT = "MyAkiyaInJapan/1.0 (+https://akiyainjapan.com; hello@akiyainjapan.com)"
REQUEST_INTERVAL_SECONDS = 1.1

# Give up on a key after this many failed attempts so repeated runs don't keep
# burning the rate limit on addresses the geocoder will never resolve.
MAX_ATTEMPTS = 3

# OSM classes that represent a place rather than a business or a building.
# "place" covers city/town/village/suburb; "boundary" covers administrative
# areas. Anything else (tourism, amenity, shop, building…) is a coincidental
# name match and must not become a pin.
ACCEPTED_CLASSES = frozenset({"place", "boundary"})

# Japan's 47 prefectures, lowercased, without the "Prefecture" suffix. Used to
# decide whether a key's trailing segment is a real prefecture worth falling
# back to. Hokkaido/Tokyo/Osaka/Kyoto keep their bare forms here because that's
# how the scraped data spells them.
PREFECTURES = frozenset(
    """
    aichi akita aomori chiba ehime fukui fukuoka fukushima gifu gunma hiroshima
    hokkaido hyogo ibaraki ishikawa iwate kagawa kagoshima kanagawa kochi
    kumamoto kyoto mie miyagi miyazaki nagano nagasaki nara niigata oita okayama
    okinawa osaka saga saitama shiga shimane shizuoka tochigi tokushima tokyo
    tottori toyama wakayama yamagata yamaguchi yamanashi
    """.split()
)


class Command(BaseCommand):
    help = "Geocode the distinct city/prefecture keys used by live properties."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0, help="Max keys this run.")
        parser.add_argument(
            "--retry",
            action="store_true",
            help="Also re-attempt keys that previously failed (under MAX_ATTEMPTS).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show which keys would be looked up, without calling the API.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        retry = options["retry"]
        dry_run = options["dry_run"]

        locations = Property.objects.filter(show_in_front=True).exclude(
            location=""
        ).values_list("location", flat=True)

        keys = sorted({k for k in (city_key(loc) for loc in locations) if k})
        self.stdout.write(f"{len(keys)} distinct city keys across live properties.")

        existing = {p.key: p for p in GeocodedPlace.objects.filter(key__in=keys)}

        todo = []
        for key in keys:
            place = existing.get(key)
            if place is None:
                todo.append(key)
            elif place.located:
                continue
            elif retry and place.attempts < MAX_ATTEMPTS:
                todo.append(key)

        located = sum(1 for p in existing.values() if p.located)
        self.stdout.write(
            f"{located} already located, {len(todo)} to look up"
            + (" (dry run)" if dry_run else "")
            + "."
        )

        if limit:
            todo = todo[:limit]
            self.stdout.write(f"Limited to {len(todo)} this run.")

        if dry_run:
            for key in todo:
                self.stdout.write(f"  would geocode: {key}")
            return

        ok = failed = 0
        for i, key in enumerate(todo, 1):
            if i > 1:
                # Rate limit between requests, not after the last one.
                time.sleep(REQUEST_INTERVAL_SECONDS)

            place, _ = GeocodedPlace.objects.get_or_create(key=key)
            result = self._geocode(key)
            place.attempts += 1
            place.checked_at = timezone.now()

            if result:
                place.latitude, place.longitude, place.display_name = result
                ok += 1
                self.stdout.write(
                    f"  [{i}/{len(todo)}] {key} -> "
                    f"{place.latitude:.4f}, {place.longitude:.4f}"
                )
            else:
                failed += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"  [{i}/{len(todo)}] {key} -> no match "
                        f"(attempt {place.attempts})"
                    )
                )
            place.save()

        self.stdout.write(self.style.SUCCESS(f"Done. {ok} located, {failed} failed."))

    def _geocode(self, key):
        """Try progressively looser forms of the key until one matches.

        The scraped keys carry translated English suffixes — "Kushiro City,
        Hokkaido" — which Nominatim does not match even though it knows
        "Kushiro, Hokkaido" perfectly well. Stripping the administrative words
        is worth far more than any other tweak here: it took the local hit rate
        from roughly 70% to nearly all legitimate keys.

        The last resort is the prefecture alone, so a property with a mangled
        city still lands in the right part of Japan rather than vanishing off
        the map.
        """
        for candidate in self._query_variants(key):
            result = self._lookup(candidate)
            if result:
                return result
            # Only rate-limit between real requests.
            time.sleep(REQUEST_INTERVAL_SECONDS)
        return None

    @staticmethod
    def _query_variants(key):
        """Ordered, de-duplicated query strings to try for one key."""
        variants = [key]

        # Drop English administrative nouns: "Kushiro City, Hokkaido" ->
        # "Kushiro, Hokkaido".
        stripped = re.sub(
            r"\b(City|Prefecture|District|Town|Village|Ward|County)\b",
            "",
            key,
            flags=re.IGNORECASE,
        )
        # Collapse the whitespace and stray punctuation the removal leaves.
        stripped = re.sub(r"\s+", " ", stripped)
        stripped = re.sub(r"\s*,\s*", ", ", stripped).strip(" ,-")
        if stripped and stripped != key:
            variants.append(stripped)

        # Romanised district/ward suffixes Nominatim also tends to miss.
        no_gun = re.sub(r"-(gun|ku|cho|machi|mura|shi)\b", "", stripped,
                        flags=re.IGNORECASE).strip(" ,-")
        if no_gun and no_gun not in variants:
            variants.append(no_gun)

        # Prefecture only — coarse, but keeps the property on the map. Only
        # when the trailing segment really is a prefecture: scraped locations
        # sometimes end in junk ("Hokkaido, and 1 other") or run
        # prefecture-first, and querying that tail is worse than not trying.
        parts = [p.strip() for p in stripped.split(",") if p.strip()]
        if len(parts) >= 2:
            tail = parts[-1]
            if tail.lower() in PREFECTURES and tail not in variants:
                variants.append(tail)

        seen = set()
        return [v for v in variants if v and not (v in seen or seen.add(v))]

    def _lookup(self, query):
        """One Nominatim call. Returns (lat, lng, display_name) or None.

        Constrained to Japan so a name that also exists elsewhere (plenty of
        Japanese city names do) can't drop a pin on the wrong continent.
        """
        params = {
            "q": query,
            "format": "json",
            # Ask for several so a populated place can outrank an incidentally
            # similarly-named business (see ACCEPTED_CLASSES).
            "limit": 5,
            "countrycodes": "jp",
            "addressdetails": 0,
        }
        key = query
        try:
            response = requests.get(
                NOMINATIM_URL,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=15,
            )
            response.raise_for_status()
            results = response.json()
        except Exception as e:
            self.stderr.write(f"    request failed for {key!r}: {e}")
            return None

        if not results:
            return None

        # Only accept populated places and administrative boundaries. Without
        # this, "Takigawa, Hokkaido" resolved to ホテル滝川 — a hotel 200km from
        # the city of the same name — and produced a confidently wrong pin.
        # Rejecting it lets the next variant (or the prefecture fallback) run,
        # which is coarse but correct.
        top = next(
            (r for r in results if r.get("class") in ACCEPTED_CLASSES), None
        )
        if top is None:
            self.stderr.write(
                f"    {query!r} only matched non-place results "
                f"({', '.join(sorted({r.get('class', '?') for r in results}))})"
            )
            return None

        try:
            lat = float(top["lat"])
            lng = float(top["lon"])
        except (KeyError, TypeError, ValueError):
            return None

        # Sanity-check against Japan's bounding box: a match outside it is a bad
        # match however confident the geocoder sounds.
        if not (24.0 <= lat <= 46.0 and 122.0 <= lng <= 154.0):
            self.stderr.write(f"    {key!r} matched outside Japan ({lat}, {lng})")
            return None

        return lat, lng, top.get("display_name", "")[:500]
