"""SUUMO (suumo.jp) — second-hand detached houses (中古一戸建て)."""
from __future__ import annotations

import re
import urllib.parse

from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

from scrapper.constants import PREFECTURE_JIS_CODE
from scrapper.scrapper import fetch, parse_jp_date, parse_jpy_price, safe_translate

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
        # Rows SUUMO has published for a while and we were walking past. For a
        # 50-year-old house these are what a buyer asks about first: has it been
        # renovated, is it warm, what does it cost to run — and when was the
        # listing actually posted, which is a different date from when we
        # happened to scrape it.
        "リフォーム", "目安光熱費", "断熱性能", "エネルギー消費性能",
        "情報提供日",
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


# その他概要・特記事項 packs several labelled segments into one cell, separated by
# '、' — e.g. '担当者：担当者制、設備：都市ガス／公共水道／公共下水'. The 設備
# segment is the one that matters: city gas versus propane, mains water versus a
# well, public sewer versus a septic tank. On an old rural house those three
# facts decide what the place costs to make habitable, and they were being stored
# with "Person in charge: assigned agent" glued to the front.
_EQUIPMENT_SEGMENT = re.compile(r"設備\s*[：:]\s*([^、,]+)")


def _extract_equipment(raw: str) -> str:
    """Pull the 設備 segment out of the notes cell. Run before translation.

    Segmenting the Japanese is reliable; segmenting the translation is not — the
    translator moves the labels around and sometimes merges the clauses.
    """
    if not raw:
        return ""
    match = _EQUIPMENT_SEGMENT.search(raw)
    return match.group(1).strip() if match else ""


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


def parse_listing(url: str, translate: bool = True) -> dict | None:
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

    # SUUMO references each listing photo two ways on a detail page:
    #   1) the clean full-size original under suumo.jp/front/gazo/bukken/...
    #   2) img01.suumo.com/jj/resizeImage?src=<url-encoded path>&w=&h=
    # The resizeImage renders carry a baked-in white frame (the "white
    # rectangle" look), so we always store form (1). When a photo only
    # appears as a resizeImage URL, its src decodes to the same
    # gazo/bukken/... path, so we rebuild the clean original from it:
    #   https://suumo.jp/front/<decoded src>
    # Each photo appears several times, so dedupe by filename in page order.
    image_urls: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        key = url.rsplit("/", 1)[-1].split("?")[0]
        if key and key not in seen:
            seen.add(key)
            image_urls.append(url)

    for match in re.findall(
        r"https?://suumo\.jp/front/gazo/bukken/[^\"'\s]+\.(?:jpg|jpeg|png)",
        response.text,
    ):
        _add(match)

    for enc in re.findall(
        r"resizeImage\?src=(gazo%2[Ff]bukken[^&\"'\s]+)", response.text
    ):
        _add("https://suumo.jp/front/" + urllib.parse.unquote(enc))

    # translate=False returns the raw Japanese untouched. Repairing one bad
    # field does not need the other twenty-two re-translated, and issuing those
    # calls anyway is what rate-limited the translator into returning error
    # pages — the exact failure the repair exists to clean up.
    # What SUUMO calls each photo. Every image on a detail page is categorised:
    # the property's own (間取り図 the floor plan, リビング, キッチン, 浴室,
    # 現地外観写真 the exterior) and the surroundings section it appends
    # (スーパー, 駅, 小学校, 病院), plus 担当者 for the agent's headshot.
    #
    # Two places carry it, and both are needed. The carousel's anchor holds
    # data-category beside a data-src, and that is the authoritative name. The
    # <img> inside it repeats it as alt — but lazily, with the URL in `rel`
    # rather than src, which is how a first attempt at this read the page and
    # saw only the surroundings thumbnails: those are the ones with a real src.
    # The floor plan came back unlabelled, and it is the most clearly labelled
    # image on the page.
    #
    # Matched by filename because the same photo appears as a clean gazo/bukken
    # original and inside a resizeImage src, and only one of the two is stored.
    def _photo_key(value):
        if isinstance(value, (list, tuple)):
            value = " ".join(value)
        found = re.search(
            r"(gazo%2[Ff]bukken[^&\"']+|gazo/bukken/[^\"'?]+)", value or ""
        )
        if not found:
            return None
        return urllib.parse.unquote(found.group(1)).rsplit("/", 1)[-1]

    photo_labels: dict[str, str] = {}
    for anchor in soup.find_all(attrs={"data-category": True}):
        name = _photo_key(anchor.get("data-src") or anchor.get("href") or "")
        if name:
            photo_labels.setdefault(name, (anchor["data-category"] or "").strip())
    for tag in soup.find_all("img"):
        alt = (tag.get("alt") or "").strip()
        if not alt:
            continue
        name = _photo_key(
            tag.get("src") or tag.get("data-src") or tag.get("rel") or ""
        )
        if name:
            photo_labels.setdefault(name, alt)

    translator = GoogleTranslator(source="auto", target="en") if translate else None

    def t(value: str | None) -> str:
        if not translate:
            return value or ""
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
        # Just the 設備 segment, not the whole notes cell — the rest is the
        # agent's contact arrangement, and it is kept in `remarks` anyway.
        "equipment": t(_extract_equipment(table.get("その他概要・特記事項", ""))),
        "transaction_type": t(table.get("取引態様", "")),
        "remarks": t(table.get("その他概要・特記事項", "")),
        "land_rights": t(table.get("土地の権利形態", "")),
        "renovation": t(table.get("リフォーム", "")),
        "estimated_utility_cost": t(table.get("目安光熱費", "")),
        "insulation_performance": t(table.get("断熱性能", "")),
        "energy_performance": t(table.get("エネルギー消費性能", "")),
        # Parsed from the raw Japanese, not the translation: see parse_jp_date.
        "listed_on": parse_jp_date(table.get("情報提供日", "")),
        "image_urls": image_urls,
        # {url: label}. Untranslated on purpose: it is matched against a fixed
        # list, never shown, and translating it would put it at the mercy of the
        # endpoint that has been returning error pages.
        "image_labels": {
            url: photo_labels.get(url.rsplit("/", 1)[-1], "") for url in image_urls
        },
    }
