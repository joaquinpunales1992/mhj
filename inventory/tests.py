"""Tests for the derivations that turn scraped prose into filterable facts.

Every string here is verbatim from the live table, not invented, because the
whole difficulty of this parser is the variety of phrasings a machine translator
produces for the same fact.

The cases that matter most are the ones that must NOT produce a number: a house
five minutes from a bus stop after a twenty minute bus ride is not five minutes
from a station, and recording it as such would put a wrong figure into a filter
people trust.
"""

from django.test import TestCase

from inventory.models import Property
from inventory.utils import parse_transit


class ParseTransitTests(TestCase):
    def test_walk_stated_before_the_station(self):
        result = parse_transit(
            "13 minutes' walk from Ozai Station on the JR Nippo Main Line"
        )
        self.assertEqual(result["station"], "Ozai")
        self.assertEqual(result["walk_minutes"], 13)
        self.assertFalse(result["needs_bus"])

    def test_the_on_foot_phrasing_and_the_operator_is_not_the_station(self):
        """"Enshu Railway" is the operator; 自動車学校前 is the station. Keeping
        the operator in the name would split one station across several labels."""
        result = parse_transit(
            "4 minutes on foot from Enshu Railway Driving School Mae Station"
        )
        self.assertEqual(result["station"], "Driving School Mae")
        self.assertEqual(result["walk_minutes"], 4)

    def test_the_hyphenated_phrasing(self):
        result = parse_transit("21-minute walk from Oita Station on the JR Nippo Main Line")
        self.assertEqual(result["station"], "Oita")
        self.assertEqual(result["walk_minutes"], 21)

    def test_station_stated_before_the_walk(self):
        result = parse_transit("JR Chitose Line Eniwa Station 4 minutes on foot")
        self.assertEqual(result["station"], "Eniwa")
        self.assertEqual(result["walk_minutes"], 4)

    def test_the_closest_station_wins(self):
        """Listings name several options; a buyer judges by the nearest."""
        result = parse_transit(
            "37 minutes on foot from Hamamatsu Station on the JR Tokaido Main Line "
            "35 minutes on foot from Takatsuka Station"
        )
        self.assertEqual(result["walk_minutes"], 35)
        self.assertEqual(result["station"], "Takatsuka")

    def test_a_distance_is_kept_separately_from_a_walk_time(self):
        result = parse_transit("JR Hakodate Main Line Goryokaku Station 4.8km")
        self.assertEqual(result["station"], "Goryokaku")
        self.assertIsNone(result["walk_minutes"])
        self.assertEqual(result["distance_km"], 4.8)

    def test_a_walk_from_a_bus_stop_is_not_a_walk_from_a_station(self):
        """The case the parser exists to get right. 'Chuo Bus ... 1 minute walk'
        means one minute from a bus stop; recorded as a station walk it would
        claim the house is a minute from rail."""
        result = parse_transit("Chuo Bus Get off at Hanakawa Minami 8jo 3-chome, 1 minute walk")
        self.assertIsNone(result["walk_minutes"])
        self.assertEqual(result["station"], "")
        self.assertTrue(result["needs_bus"])

    def test_a_bus_leg_after_a_station_does_not_become_the_walk_time(self):
        result = parse_transit(
            "JR Tokaido Main Line Shimada Station bus 32 minutes away from Kataoka "
            "and get off at Kataoka for 4 minutes on foot"
        )
        self.assertTrue(result["needs_bus"])
        self.assertIsNone(result["walk_minutes"],
                          "4 minutes is from the bus stop, not the station")

    def test_a_station_walk_is_still_found_when_a_bus_is_also_offered(self):
        """needs_bus is a fact about the description, not a veto on the walk."""
        result = parse_transit(
            "34 minutes on foot from Mikuri Station on the JR Tokaido Main Line "
            "10 minutes by bus from Fuji Station"
        )
        self.assertEqual(result["walk_minutes"], 34)
        self.assertTrue(result["needs_bus"])

    def test_a_capture_that_swallowed_prose_is_discarded(self):
        """Real mis-capture from the live data. A station whose name contains
        "minutes bus ride from" is a parsing failure, and storing it would put
        an unlookupable name in the field and an unrelated number beside it."""
        result = parse_transit(
            "1 minute on foot Kitami Station 25 minutes bus ride from Kitami "
            "Station get off at Nakanoshima"
        )
        self.assertNotIn("minutes", (result["station"] or "").lower())

    def test_empty_and_missing_input(self):
        for value in ("", None):
            result = parse_transit(value)
            self.assertEqual(result["station"], "")
            self.assertIsNone(result["walk_minutes"])
            self.assertFalse(result["needs_bus"])

    def test_an_absurd_walk_time_is_not_invented_from_a_year(self):
        """Guards against a four-digit number being read as minutes."""
        result = parse_transit("Built 1968. JR Line Adachi Station 6 minutes on foot")
        self.assertEqual(result["walk_minutes"], 6)


class TransitBackfillTests(TestCase):
    """The command that fills the fields for listings already in the table."""

    def test_it_populates_the_fields(self):
        from django.core.management import call_command
        from io import StringIO

        walkable = Property.objects.create(
            url="https://example.com/1", floor_plan="3LDK",
            traffic="9 minutes' walk from Numazu Station on the JR Tokaido Main Line",
        )
        bus = Property.objects.create(
            url="https://example.com/2", floor_plan="4LDK",
            traffic="Chuo Bus Get off at Mae, 5 minutes on foot",
        )
        call_command("parse_transit", "--all", stdout=StringIO())

        walkable.refresh_from_db()
        bus.refresh_from_db()
        self.assertEqual(walkable.nearest_station, "Numazu")
        self.assertEqual(walkable.station_walk_minutes, 9)
        self.assertFalse(walkable.needs_bus)
        self.assertIsNone(bus.station_walk_minutes)
        self.assertTrue(bus.needs_bus)

    def test_a_dry_run_writes_nothing(self):
        from django.core.management import call_command
        from io import StringIO

        property = Property.objects.create(
            url="https://example.com/3", floor_plan="2DK",
            traffic="9 minutes' walk from Numazu Station on the JR Tokaido Main Line",
        )
        out = StringIO()
        call_command("parse_transit", "--all", "--dry-run", stdout=out)
        property.refresh_from_db()
        self.assertIsNone(property.station_walk_minutes)
        self.assertIn("Dry run", out.getvalue())
