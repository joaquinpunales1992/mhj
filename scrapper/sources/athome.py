"""At Home (athome.co.jp) — second-hand detached houses (中古一戸建て)."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

from scrapper.scrapper import fetch, parse_jpy_price, safe_translate

BASE_URL = "https://www.athome.co.jp"
_LIST_TEMPLATE = BASE_URL + "/kodate/chuko/{region}/list/?page={page}"
_DETAIL_HREF_RE = re.compile(r"/kodate/(\d{8,})/")


def iter_listing_urls(region: str, page: int) -> list[str]:
    url = _LIST_TEMPLATE.format(region=region, page=page)
    response = fetch(url)
    if not response:
        return []
    ids = sorted(set(_DETAIL_HREF_RE.findall(response.text)))
    return [f"{BASE_URL}/kodate/{listing_id}/" for listing_id in ids]


def _extract_table_data(soup: BeautifulSoup) -> dict[str, str]:
    fields = {
        "所在地", "交通", "価格", "備考", "取引態様", "土地権利", "土地面積",
        "地目", "容積率", "建ぺい率", "建物構造", "建物面積", "引渡可能時期",
        "接道状況", "現況", "用途地域", "築年月", "設備・サービス", "都市計画",
        "間取り", "駐車場", "セットバック",
    }
    data: dict[str, str] = {}
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            ths = row.find_all("th")
            tds = row.find_all("td")
            for th, td in zip(ths, tds):
                key = th.get_text(" ", strip=True)
                if key in fields and key not in data:
                    data[key] = td.get_text(" ", strip=True)
    return data


def parse_listing(url: str) -> dict | None:
    response = fetch(url)
    if not response:
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""
    if not title:
        return None

    table = _extract_table_data(soup)

    image_urls = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if "athome.co.jp/image_files/" in src:
            full = src if src.startswith("http") else BASE_URL + src
            if full not in image_urls:
                image_urls.append(full)

    translator = GoogleTranslator(source="auto", target="en")

    def t(value: str | None) -> str:
        return safe_translate(value, translator=translator)

    raw_price = table.get("価格", "")
    return {
        "property_url": url,
        "property_title": t(title),
        "property_price": t(raw_price),
        "property_price_yen": parse_jpy_price(raw_price),
        "floor_plan": t(table.get("間取り", "")),
        "building_area": t(table.get("建物面積", "")),
        "land_area": t(table.get("土地面積", "")),
        "parking": t(table.get("駐車場", "")),
        "building_age": t(table.get("築年月", "")),
        "location": t(table.get("所在地", "")),
        "traffic": t(table.get("交通", "")),
        "building_structure": t(table.get("建物構造", "")),
        "road_condition": t(table.get("接道状況", "")),
        "setback": t(table.get("セットバック", "")),
        "city_planning": t(table.get("都市計画", "")),
        "zoning": t(table.get("用途地域", "")),
        "land_category": t(table.get("地目", "")),
        "building_coverage_ratio": t(table.get("建ぺい率", "")),
        "floor_area_ratio": t(table.get("容積率", "")),
        "current_status": t(table.get("現況", "")),
        "handover": t(table.get("引渡可能時期", "")),
        "equipment": t(table.get("設備・サービス", "")),
        "transaction_type": t(table.get("取引態様", "")),
        "remarks": t(table.get("備考", "")),
        "land_rights": t(table.get("土地権利", "")),
        "image_urls": image_urls,
    }
