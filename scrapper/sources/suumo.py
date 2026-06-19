"""SUUMO (suumo.jp) — second-hand detached houses (中古一戸建て)."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

from scrapper.constants import PREFECTURE_JIS_CODE
from scrapper.scrapper import fetch, parse_jpy_price, safe_translate

BASE_URL = "https://suumo.jp"

# SUUMO scopes by macro-region first (ar=...) and then filters by prefecture
# (ta=...). Querying a prefecture with the wrong ar returns 0 listings, so we
# need the prefecture → macro-region mapping. bs=021 selects 中古一戸建て.
_PREFECTURE_AR = {
    # 010 Hokkaido
    "hokkaido": "010",
    # 020 Tohoku
    "aomori": "020", "iwate": "020", "miyagi": "020", "akita": "020",
    "yamagata": "020", "fukushima": "020",
    # 030 Kanto
    "ibaraki": "030", "tochigi": "030", "gunma": "030", "saitama": "030",
    "chiba": "030", "tokyo": "030", "kanagawa": "030",
    # 040 Koshinetsu / Hokuriku (甲信越・北陸)
    "niigata": "040", "toyama": "040", "ishikawa": "040", "fukui": "040",
    "yamanashi": "040", "nagano": "040",
    # 050 Tokai (東海)
    "gifu": "050", "shizuoka": "050", "aichi": "050", "mie": "050",
    # 060 Kinki / Kansai (関西)
    "shiga": "060", "kyoto": "060", "osaka": "060", "hyogo": "060",
    "nara": "060", "wakayama": "060",
    # 070 Shikoku (四国)
    "tokushima": "070", "kagawa": "070", "ehime": "070", "kochi": "070",
    # 080 Chugoku (中国)
    "tottori": "080", "shimane": "080", "okayama": "080", "hiroshima": "080",
    "yamaguchi": "080",
    # 090 Kyushu / Okinawa (九州・沖縄)
    "fukuoka": "090", "saga": "090", "nagasaki": "090", "kumamoto": "090",
    "oita": "090", "miyazaki": "090", "kagoshima": "090", "okinawa": "090",
}

_LIST_TEMPLATE = BASE_URL + "/jj/bukken/ichiran/JJ010FJ001/?ar={ar}&bs=021&ta={code}&page={page}"

_DETAIL_HREF_RE = re.compile(r"/chukoikkodate/[a-z]+/sc_[a-z0-9]+/nc_\d+/")


def iter_listing_urls(region: str, page: int) -> list[str]:
    code = PREFECTURE_JIS_CODE.get(region)
    ar = _PREFECTURE_AR.get(region)
    if not code or not ar:
        raise ValueError(f"Unknown region {region!r}")
    url = _LIST_TEMPLATE.format(ar=ar, code=code, page=page)
    response = fetch(url)
    if not response:
        return []
    paths = sorted(set(_DETAIL_HREF_RE.findall(response.text)))
    return [BASE_URL + p for p in paths]


# SUUMO th cells include a tooltip span ("ヒント"); strip it before key lookup.
def _clean_key(text: str) -> str:
    return re.sub(r"\s*ヒント\s*$", "", text).strip()


def _extract_table_data(soup: BeautifulSoup) -> dict[str, str]:
    fields = [
        "価格", "間取り", "土地面積", "建物面積", "私道負担・道路",
        "完成時期（築年月）", "完成時期(築年月)", "住所", "所在地", "交通",
        "引渡可能時期", "土地の権利形態", "構造・工法", "用途地域", "地目",
        "建ぺい率・容積率", "その他制限事項", "その他概要・特記事項",
        "取引態様",
    ]
    data: dict[str, str] = {}
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            ths = row.find_all("th")
            tds = row.find_all("td")
            for th, td in zip(ths, tds):
                key = _clean_key(th.get_text(" ", strip=True))
                if key in fields and key not in data:
                    data[key] = td.get_text(" ", strip=True)
    return data


def _split_ratios(combined: str) -> tuple[str, str]:
    """SUUMO combines 建ぺい率・容積率 in one cell, e.g. '60％　150％'."""
    if not combined:
        return "", ""
    parts = re.findall(r"\d+(?:\.\d+)?\s*[％%]", combined)
    if len(parts) >= 2:
        return parts[0], parts[1]
    if len(parts) == 1:
        return parts[0], ""
    return combined, ""


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
    coverage, far = _split_ratios(table.get("建ぺい率・容積率", ""))
    construction = table.get("完成時期（築年月）") or table.get("完成時期(築年月)", "")
    location = table.get("住所") or table.get("所在地", "")

    # SUUMO serves listing photos two ways depending on the page:
    #   1) direct CDN originals under suumo.jp/front/gazo/bukken/...
    #   2) img01.suumo.com/jj/resizeImage?src=<url-encoded path>&w=&h=
    # Most detail pages use form (2). For those the param-free original is
    # only a 100x100 thumbnail, so we must request a sized render. Use a
    # WIDTH only (&w=1024) — NOT a fixed w&h box: a fixed box makes SUUMO
    # center smaller / non-4:3 photos on a white canvas (the "white rectangle"
    # look), whereas width-only scales proportionally with no padding. Each
    # photo appears several times at different sizes, so dedupe by the encoded
    # src and keep one entry per photo in page order.
    image_urls: list[str] = []
    seen: set[str] = set()

    for match in re.findall(
        r"https?://suumo\.jp/front/gazo/bukken/[^\"'\s]+\.(?:jpg|jpeg|png)",
        response.text,
    ):
        if match not in seen:
            seen.add(match)
            image_urls.append(match)

    for enc in re.findall(
        r"resizeImage\?src=(gazo%2[Ff]bukken[^&\"'\s]+)", response.text
    ):
        if enc not in seen:
            seen.add(enc)
            image_urls.append(
                f"https://img01.suumo.com/jj/resizeImage?src={enc}&w=1024"
            )

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
        "parking": "",
        "building_age": t(construction),
        "location": t(location),
        "traffic": t(table.get("交通", "")),
        "building_structure": t(table.get("構造・工法", "")),
        "road_condition": t(table.get("私道負担・道路", "")),
        "setback": "",
        "city_planning": t(table.get("その他制限事項", "")),
        "zoning": t(table.get("用途地域", "")),
        "land_category": t(table.get("地目", "")),
        "building_coverage_ratio": t(coverage),
        "floor_area_ratio": t(far),
        "current_status": "",
        "handover": t(table.get("引渡可能時期", "")),
        "equipment": t(table.get("その他概要・特記事項", "")),
        "transaction_type": t(table.get("取引態様", "")),
        "remarks": t(table.get("その他概要・特記事項", "")),
        "land_rights": t(table.get("土地の権利形態", "")),
        "image_urls": image_urls,
    }
