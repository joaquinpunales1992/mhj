"""Tests for the parsing that happens before translation.

The rule these enforce is the one the scraper keeps re-learning: parse the
Japanese, then translate. A translator rewrites '2026年8月19日' into prose,
drops the 万/億 markers off a price, and shuffles the labels inside a
multi-segment cell — so anything structural has to be pulled out of the raw
string first.

Every fixture here is a real value taken from a live SUUMO detail page.
"""

from datetime import date
from unittest.mock import patch

from bs4 import BeautifulSoup
from django.test import TestCase

from scrapper.scrapper import parse_jp_date
from scrapper.sources.suumo import _extract_equipment, _extract_table_data


class ParseJapaneseDateTests(TestCase):
    def test_the_listing_date_format(self):
        self.assertEqual(parse_jp_date("2026年8月19日"), date(2026, 8, 19))

    def test_slash_and_dash_forms(self):
        self.assertEqual(parse_jp_date("2026/8/19"), date(2026, 8, 19))
        self.assertEqual(parse_jp_date("2026-08-19"), date(2026, 8, 19))

    def test_an_impossible_date_is_not_invented(self):
        self.assertIsNone(parse_jp_date("2026年2月31日"))

    def test_nothing_in_nothing_out(self):
        self.assertIsNone(parse_jp_date(""))
        self.assertIsNone(parse_jp_date(None))
        self.assertIsNone(parse_jp_date("次回更新予定日未定"))


class ExtractEquipmentTests(TestCase):
    """The utilities facts, which were arriving with contact boilerplate glued on."""

    def test_the_equipment_segment_is_isolated(self):
        raw = "担当者：担当者制、設備：都市ガス／公共水道／公共下水"
        self.assertEqual(_extract_equipment(raw), "都市ガス／公共水道／公共下水")

    def test_a_well_and_septic_tank_listing(self):
        """What this field exists to surface: no mains water, no sewer."""
        raw = "担当者：担当者制、設備：プロパンガス／井戸／浄化槽"
        self.assertEqual(_extract_equipment(raw), "プロパンガス／井戸／浄化槽")

    def test_the_segment_order_does_not_matter(self):
        raw = "設備：都市ガス／公共水道、担当者：担当者制"
        self.assertEqual(_extract_equipment(raw), "都市ガス／公共水道")

    def test_a_cell_with_no_equipment_segment_yields_nothing(self):
        self.assertEqual(_extract_equipment("担当者：担当者制"), "")
        self.assertEqual(_extract_equipment(""), "")


class ExtractTableDataTests(TestCase):
    """SUUMO puts a tooltip span inside each <th>, so every label arrives with
    'ヒント' attached. The five rows added here are only reachable once that is
    stripped, which is what makes this worth a test."""

    FIXTURE = """
    <table>
      <tr><th>リフォーム ヒント</th><td>2021年10月完了 内装リフォーム：壁</td></tr>
      <tr><th>目安光熱費 ヒント</th><td>約12,000円／月</td></tr>
      <tr><th>断熱性能 ヒント</th><td>断熱等性能等級4</td></tr>
      <tr><th>エネルギー消費性能 ヒント</th><td>一次エネルギー消費量等級5</td></tr>
      <tr><th>情報提供日</th><td>2026年8月19日</td></tr>
      <tr><th>価格 ヒント</th><td>1760万円</td></tr>
    </table>
    """

    def setUp(self):
        self.table = _extract_table_data(BeautifulSoup(self.FIXTURE, "html.parser"))

    def test_the_five_new_rows_are_captured(self):
        self.assertEqual(self.table["リフォーム"], "2021年10月完了 内装リフォーム：壁")
        self.assertEqual(self.table["目安光熱費"], "約12,000円／月")
        self.assertEqual(self.table["断熱性能"], "断熱等性能等級4")
        self.assertEqual(self.table["エネルギー消費性能"], "一次エネルギー消費量等級5")
        self.assertEqual(self.table["情報提供日"], "2026年8月19日")

    def test_the_listing_date_survives_as_a_date(self):
        self.assertEqual(parse_jp_date(self.table["情報提供日"]), date(2026, 8, 19))

    def test_the_existing_rows_still_work(self):
        self.assertEqual(self.table["価格"], "1760万円")


class TranslationErrorPageTests(TestCase):
    """What happens when the translator answers with an apology.

    Google's endpoint returns 500 as an HTML page, not as a status
    deep-translator raises on, so the page text arrives looking exactly like a
    successful translation. Listings went to the site titled "Error 500 (Server
    Error)!!1500.That's an error..." — and since get_title_for_front cuts at 20
    characters, a row of cards read "Error 500 (Server Er...".

    The giveaway that it was the translator and not the listing site: the
    damage is per-field. One row kept a correct location and lost its title,
    another kept its title and lost the location. A failed page fetch cannot do
    that — parse_listing returns None and the row is never written.
    """

    # As stored, once the translator had stripped the tags out of it.
    ERROR_PAGE = (
        "Error 500 (Server Error)!!1500.That’s an error.There was an error. "
        "Please try again later.That’s all we know."
    )

    class FakeTranslator:
        def __init__(self, answer):
            self.answer = answer
            self.asked = []

        def translate(self, text):
            self.asked.append(text)
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer

    def translate(self, value, answer):
        from scrapper.scrapper import safe_translate

        return safe_translate(value, translator=self.FakeTranslator(answer))

    def test_the_error_page_is_recognised(self):
        from scrapper.scrapper import looks_like_an_error_page

        self.assertTrue(looks_like_an_error_page(self.ERROR_PAGE))

    def test_a_straight_apostrophe_is_recognised_too(self):
        """The page uses a typographic apostrophe; betting that it always will
        is not worth the one extra marker."""
        from scrapper.scrapper import looks_like_an_error_page

        self.assertTrue(looks_like_an_error_page(
            self.ERROR_PAGE.replace("’", "'")))

    def test_a_real_translation_is_not(self):
        from scrapper.scrapper import looks_like_an_error_page

        self.assertFalse(looks_like_an_error_page(
            "Detached house in Itoigawa, Niigata Prefecture"))

    def test_a_listing_that_mentions_an_error_is_not(self):
        """The markers are Google's sentences, not the word 'error'."""
        from scrapper.scrapper import looks_like_an_error_page

        self.assertFalse(looks_like_an_error_page(
            "Please report any error in this listing to the agent."))

    def test_an_error_page_is_never_returned_as_a_translation(self):
        self.assertEqual(
            self.translate("新潟県糸魚川市の中古一戸建て", self.ERROR_PAGE),
            "新潟県糸魚川市の中古一戸建て",
        )

    def test_a_real_translation_is_returned(self):
        self.assertEqual(self.translate("中古一戸建て", "Used detached house"),
                         "Used detached house")

    def test_a_raised_error_still_keeps_the_original(self):
        self.assertEqual(
            self.translate("中古一戸建て", RuntimeError("connection reset")),
            "中古一戸建て",
        )

    def test_nothing_in_gives_nothing_back(self):
        self.assertEqual(self.translate("", "anything"), "")


class RepairTranslationsTests(TestCase):
    """The command that cleans up the rows written before the guard existed."""

    ERROR_PAGE = TranslationErrorPageTests.ERROR_PAGE

    def listing(self, **fields):
        from inventory.models import Property

        values = {"url": "https://www.homes.co.jp/kodate/b-1/", "price": 200}
        values.update(fields)
        return Property.objects.create(**values)

    def run_command(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("repair_translations", *args, stdout=out)
        return out.getvalue()

    def test_it_finds_a_poisoned_row(self):
        from inventory.management.commands.repair_translations import (
            poisoned_properties,
        )

        bad = self.listing(title=self.ERROR_PAGE)
        self.listing(url="https://www.homes.co.jp/kodate/b-2/", title="A real house")
        self.assertEqual([p.pk for p in poisoned_properties()], [bad.pk])

    def test_it_reports_which_field_is_poisoned(self):
        from inventory.management.commands.repair_translations import poisoned_fields

        prop = self.listing(title="A real house", location=self.ERROR_PAGE)
        self.assertEqual(poisoned_fields(prop), ["location"])

    def test_a_dry_run_writes_nothing(self):
        prop = self.listing(title=self.ERROR_PAGE)
        self.run_command("--dry-run")
        prop.refresh_from_db()
        self.assertEqual(prop.title, self.ERROR_PAGE)

    def test_it_clears_a_field_it_cannot_re_read(self):
        """No parser exists for homes.co.jp — those rows predate the current
        scraper. An empty title is a smaller lie than Google's apology."""
        prop = self.listing(title=self.ERROR_PAGE)
        self.run_command()
        prop.refresh_from_db()
        self.assertEqual(prop.title, "")

    def test_it_leaves_the_rest_of_the_row_alone(self):
        """Only the poisoned fields. Anything curated by hand survives."""
        prop = self.listing(title=self.ERROR_PAGE, location="Aoi Ward, Shizuoka",
                            price=2880, featured=True)
        self.run_command()
        prop.refresh_from_db()
        self.assertEqual(prop.location, "Aoi Ward, Shizuoka")
        self.assertEqual(prop.price, 2880)
        self.assertTrue(prop.featured)

    def test_a_clean_database_is_left_alone(self):
        prop = self.listing(title="A real house")
        output = self.run_command()
        prop.refresh_from_db()
        self.assertEqual(prop.title, "A real house")
        self.assertIn("0 properties", output)


class RepairSafetyTests(TestCase):
    """The repair must not do damage when the translator is failing.

    The first version did. Run against 193 rows it re-parsed each listing —
    about twenty-three translation calls apiece — the translator began
    rate-limiting, and safe_translate correctly handed back the untranslated
    Japanese, which the command then stored. A row holding Japanese no longer
    matches the error markers, so those rows could not be found or repaired
    afterwards. It destroyed the thing it was there to fix.
    """

    ERROR_PAGE = TranslationErrorPageTests.ERROR_PAGE

    def listing(self, url="https://suumo.jp/chukoikkodate/nc_1/", **fields):
        from inventory.models import Property

        values = {"url": url, "price": 200, "title": self.ERROR_PAGE}
        values.update(fields)
        return Property.objects.create(**values)

    def run_command(self, *args, **kwargs):
        from io import StringIO

        from django.core.management import call_command

        out, err = StringIO(), StringIO()
        call_command("repair_translations", *args, stdout=out, stderr=err, **kwargs)
        return out.getvalue() + err.getvalue()

    def test_a_share_of_japanese_means_untranslated(self):
        from inventory.management.commands.repair_translations import (
            looks_untranslated,
        )

        self.assertTrue(looks_untranslated("永興2丁目　中古"))
        self.assertTrue(looks_untranslated("北海道釧路市文苑4丁目"))

    def test_a_stray_kanji_in_english_is_not(self):
        """An address can keep a character and still be translated. Treating
        that as failure would refuse good translations."""
        from inventory.management.commands.repair_translations import (
            looks_untranslated,
        )

        self.assertFalse(looks_untranslated(
            "25 minutes by bus from Kushiro Station on the JR Nemuro Main線"))
        self.assertFalse(looks_untranslated("104.6m2 (31.64 tsubo)"))

    def test_it_refuses_to_run_when_the_translator_is_down(self):
        prop = self.listing()
        with patch("inventory.management.commands.repair_translations."
                   "translator_is_working", return_value=False), \
             patch("scrapper.sources.suumo.parse_listing",
                   return_value={"property_title": "成田町１"}):
            output = self.run_command()
        prop.refresh_from_db()
        self.assertEqual(prop.title, self.ERROR_PAGE, "nothing may be written")
        self.assertIn("not translating", output)

    def test_a_japanese_answer_is_never_stored(self):
        """safe_translate returns the original when it fails. Storing that is
        what made the damaged rows unfindable."""
        prop = self.listing()
        with patch("inventory.management.commands.repair_translations."
                   "translator_is_working", return_value=True), \
             patch("scrapper.sources.suumo.parse_listing",
                   return_value={"property_title": "成田町１（岡谷駅） 390万円"}), \
             patch("scrapper.scrapper.safe_translate",
                   side_effect=lambda v, translator=None: v):
            output = self.run_command()
        prop.refresh_from_db()
        self.assertEqual(prop.title, self.ERROR_PAGE,
                         "the row stays findable rather than holding Japanese")
        self.assertIn("left alone", output)

    def test_it_stops_once_the_translator_is_clearly_rate_limiting(self):
        for n in range(5):
            self.listing(url=f"https://suumo.jp/chukoikkodate/nc_{n}/")
        with patch("inventory.management.commands.repair_translations."
                   "translator_is_working", return_value=True), \
             patch("scrapper.sources.suumo.parse_listing",
                   return_value={"property_title": "成田町１"}), \
             patch("scrapper.scrapper.safe_translate",
                   side_effect=lambda v, translator=None: v), \
             patch("inventory.management.commands.repair_translations."
                   "REQUEST_INTERVAL_SECONDS", 0):
            output = self.run_command()
        self.assertIn("rate-limiting", output)

    def test_a_real_translation_is_stored(self):
        prop = self.listing()
        with patch("inventory.management.commands.repair_translations."
                   "translator_is_working", return_value=True), \
             patch("scrapper.sources.suumo.parse_listing",
                   return_value={"property_title": "成田町１（岡谷駅）"}), \
             patch("scrapper.scrapper.safe_translate",
                   side_effect=lambda v, translator=None: "Narita-cho 1 (Okaya Station)"):
            self.run_command()
        prop.refresh_from_db()
        self.assertEqual(prop.title, "Narita-cho 1 (Okaya Station)")

    def test_the_listing_is_parsed_without_translating_it(self):
        """One call for the broken field, not twenty-three for the whole row."""
        self.listing()
        with patch("inventory.management.commands.repair_translations."
                   "translator_is_working", return_value=True), \
             patch("scrapper.sources.suumo.parse_listing") as parse, \
             patch("scrapper.scrapper.safe_translate", return_value="A house"):
            parse.return_value = {"property_title": "家"}
            self.run_command()
        self.assertEqual(parse.call_args.kwargs.get("translate"), False)


class RepairUnreachableTests(RepairSafetyTests):
    """A listing we cannot reach is not a listing that is gone."""

    def test_an_unreachable_listing_is_left_alone_not_cleared(self):
        """parse_listing returns None for a 404, a block and a dropped
        connection alike. Clearing on that would blank every title the moment
        SUUMO starts rate-limiting the scraper."""
        prop = self.listing()
        with patch("scrapper.sources.suumo.parse_listing", return_value=None):
            output = self.run_command()
        prop.refresh_from_db()
        self.assertEqual(prop.title, self.ERROR_PAGE)
        self.assertIn("could not re-read", output)
