"""Finding something worth saying that happened outside our own database.

Two kinds of feed, for one annoying reason: Google News gives the widest net but
its links are opaque tokens that only resolve in a browser, so a story found
there can be attributed but not linked. Publisher feeds carry a real URL but
cover all of Japan, so they are keyword-filtered.

Neither kind gives us the article body, and we do not fetch it — we have not
read the piece, so the copy is written as our reaction to a headline someone
else reported, never as reporting of our own. That is also why every news post
names the outlet on the card itself and not only in the caption.
"""

import hashlib
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone as dt_timezone
from email.utils import parsedate_to_datetime

import requests

from social.constants import (
    NEWS_FEED_URL,
    NEWS_KEYWORDS_STRONG,
    NEWS_KEYWORDS_WEAK,
    NEWS_MAX_AGE_DAYS,
    NEWS_MIN_HEADLINE_CHARS,
    NEWS_QUERIES_BROAD,
    NEWS_QUERIES_SPECIFIC,
    NEWS_RSS_FEEDS,
    NEWS_SOURCE_BLOCKLIST,
)
from social.content.material import Material
from social.models import SocialPost

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
TIMEOUT = 25

# Context the copy may lean on, so "our take" is not simply the headline said
# twice. These are the same durable facts the FAQ bank states, kept short.
MARKET_CONTEXT = [
    "Japan has millions of vacant houses, mostly because of an ageing and "
    "shrinking rural population rather than anything wrong with the houses.",
    "Foreigners can buy property in Japan with no residency requirement, and "
    "buying grants no visa.",
    "On a cheap rural house, renovation often costs more than the purchase.",
]


def _get(url, params=None):
    response = requests.get(
        url, params=params, timeout=TIMEOUT, headers={"User-Agent": UA}
    )
    response.raise_for_status()
    return response.content


def _published(item):
    """Parse whatever date format the feed felt like using."""
    raw = item.findtext("pubDate") or item.findtext(
        "{http://purl.org/dc/elements/1.1/}date"
    )
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except Exception:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


def _clean_headline(title):
    """Strip what feeds put in a title that is not the headline.

    Some feeds append the article URL to the title, which then ends up drawn
    onto the card. Long titles are cut at a sentence boundary rather than
    mid-word: a card can shrink type to fit anything, but a headline that runs
    for two sentences is not a headline.
    """
    text = re.sub(r"https?://\S+", "", title)
    text = re.sub(r"\s+", " ", text).strip(" -–—|")
    if len(text) <= 140:
        return text
    cut = text[:140]
    for stop in (". ", "? ", "! "):
        if stop in cut:
            return cut[: cut.rindex(stop) + 1].strip()
    return cut.rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _outlet_from_title(title):
    """Google News appends ' - Outlet' to every headline. Split it off.

    rsplit on the last ' - ' rather than the first: plenty of headlines contain
    a dash of their own, and only the trailing one is the outlet.
    """
    if " - " in title:
        headline, outlet = title.rsplit(" - ", 1)
        if 2 <= len(outlet) <= 40:
            return headline.strip(), outlet.strip()
    return title.strip(), ""


def _blocked(outlet):
    lowered = (outlet or "").lower()
    return any(bad in lowered for bad in NEWS_SOURCE_BLOCKLIST)


def _relevant(text):
    """One strong term, or two weak ones corroborating each other.

    A flat keyword list let "real estate" alone qualify a story about a ninja
    theme park closing over a property dispute. Technically about real estate;
    not what anyone follows this account for.
    """
    lowered = text.lower()
    if any(keyword in lowered for keyword in NEWS_KEYWORDS_STRONG):
        return True
    return sum(keyword in lowered for keyword in NEWS_KEYWORDS_WEAK) >= 2


def _story_key(headline):
    """Identity of a story, so the same one from three feeds counts once.

    Keyed on the headline's words rather than the URL: syndicated pieces appear
    under several URLs, and Google News hands us no usable URL at all.
    """
    words = re.findall(r"[a-z0-9]+", headline.lower())
    digest = hashlib.sha1(" ".join(words[:12]).encode()).hexdigest()[:16]
    return f"news:{digest}"


def _already_posted_keys():
    """News never repeats, so this looks at all of time rather than a window."""
    from social.models import ContentDraft

    return set(
        ContentDraft.objects.filter(kind=SocialPost.KIND_NEWS).values_list(
            "key", flat=True
        )
    )


def _items_from_google(query, trusted):
    params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    root = ET.fromstring(_get(NEWS_FEED_URL, params))
    for item in root.findall(".//item"):
        title = _clean_headline(item.findtext("title") or "")
        if not title:
            continue
        headline, outlet = _outlet_from_title(title)
        source_el = item.find("source")
        if source_el is not None and source_el.text:
            outlet = source_el.text.strip()
        yield {
            "headline": headline,
            "outlet": outlet,
            "published": _published(item),
            "link": "",  # Google's link is a token no server can resolve
            "needs_keyword_match": not trusted,
        }


def _items_from_feed(outlet, url):
    root = ET.fromstring(_get(url))
    for item in root.findall(".//item"):
        title = _clean_headline(item.findtext("title") or "")
        if not title:
            continue
        yield {
            "headline": title,
            "outlet": outlet,
            "published": _published(item),
            "link": (item.findtext("link") or "").strip(),
            "needs_keyword_match": True,
        }


def gather():
    """Every news story we could post right now, freshest first.

    A feed that fails is logged and skipped: one publication being down is not
    a reason for the account to say nothing today.
    """
    cutoff = datetime.now(dt_timezone.utc) - timedelta(days=NEWS_MAX_AGE_DAYS)
    seen_keys = _already_posted_keys()
    found = {}

    feeds = [("google", query, None, True) for query in NEWS_QUERIES_SPECIFIC]
    feeds += [("google", query, None, False) for query in NEWS_QUERIES_BROAD]
    feeds += [("rss", outlet, url, False) for outlet, url in NEWS_RSS_FEEDS]

    for feed_type, label, url, trusted in feeds:
        try:
            items = (
                _items_from_google(label, trusted)
                if feed_type == "google"
                else _items_from_feed(label, url)
            )
            items = list(items)
        except Exception as exc:
            logger.warning("News feed %s failed: %s", label, exc)
            continue

        for item in items:
            headline = item["headline"]
            if len(headline) < NEWS_MIN_HEADLINE_CHARS:
                continue
            if _blocked(item["outlet"]):
                continue
            if item["needs_keyword_match"] and not _relevant(headline):
                continue
            published = item["published"]
            if not published or published < cutoff:
                continue

            key = _story_key(headline)
            if key in seen_keys or key in found:
                # Prefer the copy that came with a real link, since Facebook
                # can use it.
                if key in found and item["link"] and not found[key]["link"]:
                    found[key] = item
                continue
            found[key] = item

    materials = []
    for key, item in found.items():
        outlet = item["outlet"] or "the press"
        materials.append(
            Material(
                kind=SocialPost.KIND_NEWS,
                key=key,
                headline=item["headline"],
                facts=[
                    f'{outlet} published a story headlined: "{item["headline"]}"'
                ] + MARKET_CONTEXT,
                medium="carousel",
                eyebrow="In the news",
                body_eyebrow="What it means",
                footnote=f"Reported by {outlet}",
                link=item["link"],
                cooldown_days=None,  # a story is posted once, ever
                brief=(
                    "This is a headline someone else reported and you have NOT "
                    "read the article. Write our reaction: what this means for "
                    "someone thinking about buying a cheap house in Japan. Do "
                    "not state any detail of the story beyond the headline "
                    "itself, do not name people, places or figures that are not "
                    "in it, and do not write as though we reported it."
                ),
                meta={"published": item["published"], "outlet": outlet},
            )
        )

    materials.sort(key=lambda m: m.meta["published"], reverse=True)
    logger.info("News: %s postable stories", len(materials))
    return materials
