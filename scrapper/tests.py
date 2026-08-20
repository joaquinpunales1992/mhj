"""Tests for the parsing that happens before translation.

The rule these enforce is the one the scraper keeps re-learning: parse the
Japanese, then translate. A translator rewrites '2026年8月19日' into prose,
drops the 万/億 markers off a price, and shuffles the labels inside a
multi-segment cell — so anything structural has to be pulled out of the raw
string first.

Every fixture here is a real value taken from a live SUUMO detail page.
"""

from datetime import date

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
