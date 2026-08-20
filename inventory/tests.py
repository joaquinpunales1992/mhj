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


class DeskReportRuleTests(TestCase):
    """The rules that turn stored fields into findings.

    The assertions worth having here are the refusals: a rule that invents a
    utilities arrangement, or promotes a designation into a determination, turns
    a paid report into a liability.
    """

    def make(self, **fields):
        defaults = dict(url="https://example.com/x", floor_plan="3LDK", price=2249)
        defaults.update(fields)
        return Property.objects.create(**defaults)

    def findings(self, property):
        from inventory.desk_report import build_report

        return {f["title"]: f for f in build_report(property)["findings"]}

    def severity_of(self, property, fragment):
        for title, finding in self.findings(property).items():
            if fragment.lower() in title.lower():
                return finding["severity"]
        return None

    # --- deal-breakers ---------------------------------------------------

    def test_an_urbanization_control_area_is_critical(self):
        property = self.make(city_planning="Urbanization control area")
        finding = self.findings(property)["Inside an urbanization control area"]
        self.assertEqual(finding["severity"], "critical")
        self.assertEqual(finding["source_label"], "都市計画")

    def test_the_control_area_finding_asks_rather_than_concludes(self):
        """It must not tell the reader they cannot rebuild — only the municipal
        office can say that, and being wrong either kills a good purchase or
        endorses a bad one."""
        property = self.make(city_planning="市街化調整区域")
        finding = self.findings(property)["Inside an urbanization control area"]
        text = " ".join(finding["body"]).lower()
        self.assertIn("in general", text)
        self.assertIn("in writing", text)
        self.assertTrue(finding["questions"])

    def test_farmland_is_critical(self):
        property = self.make(land_category="Farmland")
        self.assertEqual(self.severity_of(property, "farmland"), "critical")

    def test_leasehold_land_is_critical(self):
        property = self.make(land_rights="Normal lease rights")
        self.assertEqual(self.severity_of(property, "leasehold"), "critical")

    def test_freehold_with_a_residential_category_clears(self):
        property = self.make(land_rights="Ownership", land_category="Residence")
        finding = self.findings(property)["Land tenure is freehold"]
        self.assertEqual(finding["severity"], "cleared")
        self.assertIn("not farmland", " ".join(finding["body"]))

    # --- utilities -------------------------------------------------------

    def test_a_missing_utilities_line_is_reported_as_missing(self):
        property = self.make(equipment="")
        finding = self.findings(property)["Water, sewer and gas are not disclosed"]
        self.assertEqual(finding["severity"], "unknown")
        self.assertIn("no value published", finding["source_value"])

    def test_a_dash_counts_as_missing(self):
        """homes.co.jp publishes '-' for this field, which is not an answer."""
        property = self.make(equipment="-")
        self.assertEqual(self.severity_of(property, "not disclosed"), "unknown")

    def test_mains_services_clear(self):
        property = self.make(equipment="City gas/public water supply/public sewage")
        finding = self.findings(property)["On mains services"]
        self.assertEqual(finding["severity"], "cleared")

    def test_a_well_and_septic_tank_are_flagged_with_what_they_are(self):
        property = self.make(equipment="プロパンガス／井戸／浄化槽")
        finding = next(f for t, f in self.findings(property).items()
                       if "Off-grid" in t)
        self.assertEqual(finding["severity"], "caution")
        joined = finding["title"] + " ".join(finding["body"])
        self.assertIn("well", joined)
        self.assertIn("septic", joined)
        self.assertIn("propane", joined)

    # --- age and structure -----------------------------------------------

    def test_a_pre_1981_building_is_flagged(self):
        property = self.make(construction_date="1976年7月（築49年）")
        finding = self.findings(property)["Built 1976 — before the current earthquake standard"]
        self.assertEqual(finding["severity"], "caution")

    def test_the_age_parse_is_not_fooled_by_the_bracketed_age(self):
        property = self.make(construction_date="2005年3月（築20年）")
        self.assertIsNone(self.severity_of(property, "earthquake standard"),
                          "a 2005 building is post-standard")

    def test_documented_seismic_work_changes_the_advice(self):
        with_work = self.make(
            construction_date="1976年7月",
            description="●Earthquake-resistant reinforcement work was carried out",
        )
        without = self.make(url="https://example.com/y", construction_date="1976年7月")
        with_text = " ".join(
            self.findings(with_work)["Built 1976 — before the current earthquake standard"]["body"]
        )
        without_text = " ".join(
            self.findings(without)["Built 1976 — before the current earthquake standard"]["body"]
        )
        self.assertIn("Mitigated here", with_text)
        self.assertIn("No reinforcement work is mentioned", without_text)

    # --- access ----------------------------------------------------------

    def test_a_short_walk_clears_and_a_long_one_cautions(self):
        near = self.make(nearest_station="Numazu", station_walk_minutes=9)
        far = self.make(url="https://example.com/z", nearest_station="Bungo Kokubun",
                        station_walk_minutes=32)
        self.assertEqual(self.severity_of(near, "walk"), "cleared")
        self.assertEqual(self.severity_of(far, "walk"), "caution")

    def test_bus_only_access_is_a_caution(self):
        property = self.make(needs_bus=True, traffic="Chuo Bus Get off at Mae")
        self.assertEqual(self.severity_of(property, "no walkable station"), "caution")

    # --- staleness -------------------------------------------------------

    def test_a_past_handover_date_is_flagged(self):
        property = self.make(handover="July 2025")
        finding = self.findings(property)["Stale listing"]
        self.assertEqual(finding["severity"], "caution")
        self.assertIn("in the past", " ".join(finding["body"]))

    def test_a_future_handover_date_is_not_flagged(self):
        property = self.make(handover="December 2030")
        self.assertIsNone(self.severity_of(property, "stale"))

    # --- assembly --------------------------------------------------------

    def test_blocking_findings_are_the_critical_and_unstated_ones(self):
        from inventory.desk_report import build_report

        property = self.make(city_planning="Urbanization control area", equipment="")
        report = build_report(property)
        self.assertEqual(len(report["blocking"]), 2)
        self.assertEqual(report["findings"][0]["severity"], "critical",
                         "the worst finding must lead")

    def test_every_report_carries_the_standard_questions(self):
        from inventory.desk_report import STANDARD_QUESTIONS, build_report

        report = build_report(self.make())
        for question in STANDARD_QUESTIONS:
            self.assertIn(question, report["questions"])

    def test_questions_are_not_duplicated(self):
        from inventory.desk_report import build_report

        report = build_report(self.make(city_planning="Urbanization control area"))
        self.assertEqual(len(report["questions"]), len(set(report["questions"])))

    def test_blank_fields_are_listed_as_withheld_not_omitted(self):
        from inventory.desk_report import build_report

        report = build_report(self.make(equipment="", insulation_performance=""))
        withheld = [row["heading"] for row in report["withheld"]]
        self.assertIn("Utilities", withheld)
        self.assertIn("Insulation", withheld)


class DeskReportCommandTests(TestCase):
    def setUp(self):
        self.property = Property.objects.create(
            url="https://example.com/report", title="Detached house in Yokose",
            price=2249, floor_plan="3LDK", location="Oita City, Oita Prefecture",
            building_area="110.78㎡", land_area="289.85㎡",
            construction_date="1976年7月（築49年）",
            city_planning="Urbanization control area", land_rights="Ownership",
            land_category="Residence", equipment="",
            road_condition="East 5.8m private road", handover="July 2025",
            nearest_station="Bungo Kokubun", station_walk_minutes=32,
        )

    def test_it_writes_a_self_contained_html_report(self):
        import os
        import tempfile
        from io import StringIO

        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "report.html")
            call_command("desk_report", self.property.pk, out=path, stdout=StringIO())
            html = open(path, encoding="utf-8").read()

        self.assertIn("Detached house in Yokose", html)
        self.assertIn("Inside an urbanization control area", html)
        self.assertIn("都市計画", html)
        self.assertIn("Take these to the agent", html)
        # No external assets beyond the font stylesheet the design depends on.
        self.assertNotIn("<script", html)

    def test_a_report_is_a_draft_until_the_human_sections_are_done(self):
        import os
        import tempfile
        from io import StringIO

        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as directory:
            draft_path = os.path.join(directory, "draft.html")
            final_path = os.path.join(directory, "final.html")
            call_command("desk_report", self.property.pk, out=draft_path,
                         stdout=StringIO())
            call_command("desk_report", self.property.pk, out=final_path,
                         final=True, verdict="Worth pursuing at a lower price.",
                         stdout=StringIO())
            draft = open(draft_path, encoding="utf-8").read()
            final = open(final_path, encoding="utf-8").read()

        self.assertIn("Not for issue", draft)
        self.assertIn("Still to complete", draft)
        self.assertNotIn("Not for issue", final)
        self.assertIn("Worth pursuing at a lower price.", final)

    def test_the_text_view_summarises_without_writing_a_file(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("desk_report", self.property.pk, text=True, stdout=out)
        output = out.getvalue()
        self.assertIn("CRITICAL", output)
        self.assertIn("urbanization control area", output)
        self.assertIn("questions for the agent", output)

    def test_an_unknown_property_is_a_clean_error(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("desk_report", 999999)
