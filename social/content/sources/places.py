"""Somewhere to talk about that is not a house.

Every other source here is anchored to a listing or to a question about buying
one, which is why the feed reads like a shop window. This one posts about the
places themselves — the prefecture, the town — from facts fetched off
Wikipedia rather than out of the model's own knowledge, for the same reason
news.py quotes a headline instead of summarising an article nobody here read.

Candidates come out of our own inventory rather than from a list of interesting
Japanese towns. A place earns a post when we have houses in it, and the count
is itself one of the facts: that keeps the format on-brand without turning it
back into a listing post, and it keeps the number of places finite and slowly
changing rather than unbounded.

Nothing here states anything Wikipedia did not, and copy.py enforces it — a
draft that invents a population, a festival or a temple is thrown away instead
of posted.
"""

import logging
import re
import unicodedata
import urllib.parse
from datetime import timedelta

import requests
from django.utils import timezone

from social.constants import (
    PLACE_COOLDOWN_DAYS,
    PLACE_MAX_PER_RUN,
    PLACE_MIN_EXTRACT_CHARS,
    PLACE_MIN_LISTINGS,
    PLACE_SEARCH_RESULTS,
)
from social.content.material import Material
from social.content.sources.news import MARKET_CONTEXT

# _live, _prefecture and _short_place are what the stats source already means by
# a live listing, by the prefecture in a scraped address, and by a place a
# reader can picture. Imported rather than rewritten: a second copy of any of
# them would drift, and it would be the copy nobody remembers to update.
from social.content.sources.stats import _live, _prefecture, _short_place
from social.models import ContentDraft, SocialPost

logger = logging.getLogger(__name__)

# Wikimedia asks automated clients to identify themselves and blocks the
# generic library user-agents, so this one names the site rather than a browser.
UA = "AkiyaInJapanBot/1.0 (https://akiyainjapan.com) python-requests"
TIMEOUT = 20

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Durable claims the copy may lean on, so a place post explains why the houses
# there are cheap instead of reading like a travel brochure. The prefecture
# count is here for a mechanical reason: copy.py discards any figure the facts
# do not contain, and "one of the 47 prefectures" is the phrase the model kept
# reaching for and losing a draft to.
PLACE_CONTEXT = MARKET_CONTEXT + ["Japan has 47 prefectures."]

# Words that mean the article is about something in a place rather than the
# place itself. Matched against the title and Wikidata's one-line description,
# never the extract: an article about a town legitimately mentions its station.
# Matched as whole words, because "line" is a substring of "coastline" and
# "shoreline", both of which turn up in a description of somewhere coastal.
NOT_A_PLACE = frozenset([
    "station", "railway", "line", "school", "university", "college",
    "airport", "river", "dam", "castle", "domain", "stadium", "tunnel",
    "bridge", "expressway", "clan", "battle", "hospital", "museum", "temple",
    "shrine", "festival", "company", "team", "album", "film", "manga",
])

BRIEF = (
    "You are writing about a place, for someone abroad who has never heard of "
    "it and is looking at cheap houses in Japan. Say what kind of place it is "
    "and what that means for someone thinking of buying there. You have never "
    "been: do not name festivals, foods, temples, stations, mountains, "
    "beaches or attractions that are not in the facts, do not describe the "
    "weather or the seasons, and do not write it as travel advice or as a "
    "recommendation to visit. Do not claim the place is famous, beautiful or "
    "undiscovered."
)


def _fold(text):
    """Ōita -> Oita.

    Wikipedia writes the macrons and our scraped addresses do not, so every
    comparison between the two has to happen with them stripped.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _key(place):
    slug = re.sub(r"[^a-z0-9]+", "-", _fold(place).lower()).strip("-")
    return f"place:{slug}"


def _get_json(url, params=None):
    response = requests.get(
        url, params=params, timeout=TIMEOUT,
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    response.raise_for_status()
    return response.json()


def _resolve_titles(query):
    """The articles Wikipedia offers for this place, best match first.

    Searched rather than constructed: the article for a town is titled
    "Tsukumi, Ōita", and a scraped address gives us no way to produce that —
    not the macron, not the comma, not which of the two names comes first.

    Several results rather than one because the best match is regularly not a
    place at all: searching for the town of Kitami returns Kitami Station, and
    taking the first hit meant losing a town we have 132 houses in.
    """
    payload = _get_json(WIKIPEDIA_API, {
        "action": "query", "list": "search", "srsearch": query,
        "srlimit": PLACE_SEARCH_RESULTS, "format": "json",
    })
    return [
        result["title"]
        for result in payload.get("query", {}).get("search") or []
        if result.get("title")
    ]


def _summary(title):
    """The article's opening, or None if the article is not one.

    A `type` other than "standard" is a disambiguation page or a stub with no
    extract, and neither is something to write a post from.
    """
    quoted = urllib.parse.quote(title.replace(" ", "_"), safe="")
    payload = _get_json(WIKIPEDIA_SUMMARY + quoted)
    return payload if payload.get("type") == "standard" else None


def _population(qid):
    """The population Wikidata records for an entity, or None.

    Preferred rank wins, then the latest "point in time" qualifier: a place
    carries a claim per census, and the first in the list is not reliably the
    newest one.
    """
    if not qid:
        return None
    payload = _get_json(WIKIDATA_API, {
        "action": "wbgetclaims", "entity": qid, "property": "P1082",
        "format": "json",
    })

    best, best_sort = None, (-1, "")
    for claim in payload.get("claims", {}).get("P1082", []):
        try:
            amount = int(float(claim["mainsnak"]["datavalue"]["value"]["amount"]))
        except (KeyError, TypeError, ValueError):
            continue
        rank = {"preferred": 2, "normal": 1}.get(claim.get("rank"), 0)
        when = ""
        for qualifier in claim.get("qualifiers", {}).get("P585", []):
            when = qualifier.get("datavalue", {}).get("value", {}).get("time", "")
        if (rank, when) > best_sort:
            best, best_sort = amount, (rank, when)
    return best


def _first_sentences(text, count=3):
    """The opening of the article, cut at a sentence boundary.

    The full extract runs to a couple of paragraphs and would fill the fact
    list with detail the copy then feels obliged to use. The first few
    sentences are the part that says what the place actually is.

    Whitespace is collapsed on the way out: an extract whose Japanese name has
    been stripped leaves a double space behind, which then gets drawn onto the
    card.
    """
    collapsed = re.sub(r"\s+", " ", text or "").strip()
    sentences = re.split(r"(?<=[.!?])\s+", collapsed)
    return " ".join(sentences[:count]).strip()


def _states_a_population(text):
    """Whether the article has already given us a population figure.

    It usually has, for anywhere big enough to be worth a post, and Wikidata's
    number is from a different census — offering the copywriter both meant
    handing it two different populations for the same town and letting it pick.
    """
    return bool(re.search(r"population(?: of| is| was)?\s+[\d,]+", text, re.I))


def _looks_like_a_place(summary):
    """Whether this article is about somewhere rather than something in it.

    Wikipedia's search happily answers a town name with its railway station,
    its high school or the river it sits on. Those articles mention Japan and
    the prefecture, so the verification below would pass them.
    """
    text = _fold(f"{summary.get('title', '')} {summary.get('description', '')}").lower()
    return not (set(re.findall(r"[a-z]+", text)) & NOT_A_PLACE)


def _candidates():
    """Places we have enough houses in to talk about, most houses first.

    Counted at two levels because they read differently: a town is the place
    someone pictures, a prefecture is the one they have a chance of having
    heard of. A listing whose address names no prefecture is skipped — without
    one there is no way to check that Wikipedia found the right place, and
    Japanese town names repeat.
    """
    towns, prefectures = {}, {}
    for location in _live().exclude(location="").values_list("location", flat=True):
        prefecture = _prefecture(location)
        if not prefecture:
            continue
        prefectures[prefecture] = prefectures.get(prefecture, 0) + 1
        place = _short_place(location)
        if place and place != prefecture:
            towns[(place, prefecture)] = towns.get((place, prefecture), 0) + 1

    # `place` is how we write it — "Tsukumi City, Oita" — and `name` is the
    # bare name Wikipedia knows it by, which is what gets looked up.
    candidates = [
        {
            "place": place,
            "name": place.split(",")[0].replace(" City", "").strip(),
            "prefecture": prefecture,
            "count": count,
        }
        for (place, prefecture), count in towns.items()
    ] + [
        {
            "place": prefecture,
            "name": prefecture,
            "prefecture": prefecture,
            "count": count,
        }
        for prefecture, count in prefectures.items()
    ]

    candidates = [c for c in candidates if c["count"] >= PLACE_MIN_LISTINGS]
    candidates.sort(key=lambda c: c["count"], reverse=True)
    for candidate in candidates:
        candidate["key"] = _key(candidate["place"])
        candidate["query"] = (
            f"{candidate['name']} Prefecture Japan"
            if candidate["place"] == candidate["prefecture"]
            else f"{candidate['name']} {candidate['prefecture']} Japan"
        )
    return candidates


def _recently_used_keys():
    """Places said recently enough that the planner would reject them anyway.

    Checked here as well as in the planner because every candidate that gets
    past this point costs two requests to Wikipedia, and the planner only sees
    material that has already been fetched.
    """
    since = timezone.now() - timedelta(days=PLACE_COOLDOWN_DAYS)
    return set(
        ContentDraft.objects.filter(
            kind=SocialPost.KIND_GUIDE, created_at__gte=since
        ).values_list("key", flat=True)
    )


def _describes_the_right_place(summary, candidate):
    """Whether the article found is about the place we asked for.

    Wikipedia's search is fuzzy and Japanese place names repeat — there is a
    Fuchu in Tokyo and a Fuchu in Hiroshima — so the first hit is a guess until
    it names both Japan and the prefecture the listing is in.
    """
    haystack = _fold(
        f"{summary.get('title', '')} {summary.get('description', '')} "
        f"{summary.get('extract', '')}"
    ).lower()
    if "japan" not in haystack:
        return False
    if _fold(candidate["prefecture"]).lower() not in haystack:
        return False
    # And the town's own name, or the prefecture's article would verify as any
    # town inside it: an article on Ōita Prefecture names Japan and Ōita, so
    # without this a post headlined "Tsukumi City" is written from it.
    return _fold(candidate["name"]).lower() in haystack


def _guessed_titles(candidate):
    """The titles this place probably has, best guess first.

    English Wikipedia names Japanese municipalities "<Town>, <Prefecture>" and
    prefectures "<Name> Prefecture", and redirects the macron-less spellings we
    hold to them. Guessing is worth doing before searching because it is right
    more often: a search for the town of Kitami returns its station, its high
    school and its institute of technology, and never the town.
    """
    name, prefecture = candidate["name"], candidate["prefecture"]
    if candidate["place"] == prefecture:
        return [f"{name} Prefecture", name]
    return [f"{name}, {prefecture}", name]


def _verified(title, candidate):
    """The summary for a title if it is this place, else None.

    Every rejection here is a silent failure being caught: a guessed title that
    does not exist, a disambiguation page, a railway station, the same town
    name in a different prefecture, a stub with nothing in it.
    """
    try:
        summary = _summary(title)
    except Exception as exc:
        # A guessed title that does not exist is a 404, and an expected one.
        logger.info("Places: could not read '%s' (%s)", title, exc)
        return None

    if not summary:
        logger.info("Places: '%s' is a disambiguation or has no extract", title)
        return None
    if not _looks_like_a_place(summary):
        logger.info("Places: '%s' is not a place", title)
        return None
    if not _describes_the_right_place(summary, candidate):
        logger.info("Places: '%s' is not the %s we meant", title, candidate["place"])
        return None
    if len(_first_sentences(summary.get("extract", ""))) < PLACE_MIN_EXTRACT_CHARS:
        # A stub leaves the copy nothing to work from, and a post written from
        # nothing is the failure this whole pipeline exists to prevent.
        logger.info("Places: the article on '%s' is too thin to post", title)
        return None
    return summary


def _article_for(candidate):
    """The article that is actually this place, as (title, summary).

    Guesses first and searches only if the guesses miss, which is both the
    accurate order and the cheap one: for anywhere Wikipedia names by the
    convention, this is a single request.
    """
    def titles():
        # A generator so the search only happens if every guess missed.
        for title in _guessed_titles(candidate):
            yield title
        yield from _resolve_titles(candidate["query"])

    tried = []
    for title in titles():
        if title in tried:
            continue
        tried.append(title)
        summary = _verified(title, candidate)
        if summary:
            # The title the article actually has, not the one we guessed:
            # "Oita Prefecture" is a redirect, and the footnote should credit
            # Ōita Prefecture the way Wikipedia spells it.
            return summary.get("title") or title, summary
    return None, None


def _material(candidate):
    """One place, as something postable — or None if it cannot be verified."""
    title, summary = _article_for(candidate)
    if not title:
        logger.info("Places: nothing verifiable for %s", candidate["query"])
        return None

    extract = _first_sentences(summary.get("extract", ""))
    place, count = candidate["place"], candidate["count"]
    located = (
        f"{place} is a prefecture of Japan."
        if place == candidate["prefecture"]
        else f"{place} is a city in {candidate['prefecture']}, Japan."
    )
    facts = [
        f'The Wikipedia article on {title} says: "{extract}"',
        located,
        f"We have {count} houses listed in {place} on akiyainjapan.com "
        "right now.",
    ]

    # Only worth asking Wikidata when the article did not already say it —
    # otherwise the fact list carries two populations from two censuses and the
    # copy picks whichever it likes.
    if not _states_a_population(extract):
        try:
            population = _population(summary.get("wikibase_item"))
        except Exception as exc:
            # Wikidata is the optional half of this: the post works without a
            # population, and one missing figure is not a reason to say nothing.
            logger.warning("Places: no population for '%s' (%s)", title, exc)
            population = None
        if population:
            facts.append(
                f"Wikidata records the population of {title} as {population:,}."
            )

    facts += PLACE_CONTEXT

    return Material(
        kind=SocialPost.KIND_GUIDE,
        key=candidate["key"],
        headline=f"{count} of our houses are in {place}",
        facts=facts,
        medium="carousel",
        eyebrow="The area",
        body_eyebrow="What it is like",
        footnote=f"Source: Wikipedia — {title}",
        link=(summary.get("content_urls", {}).get("desktop", {}).get("page", "")),
        cooldown_days=PLACE_COOLDOWN_DAYS,
        # Deliberately not flagged for review, even though these facts come
        # from outside: needs_review also appends the purchase disclaimer in
        # copy.py, and "rules and rates vary by municipality" under a post
        # about what a town is like reads like a warning about the town.
        needs_review=False,
        brief=BRIEF,
        meta={"location": place, "title": title},
    )


def gather():
    """Every place post we could make right now, most houses first.

    A candidate that fails to verify is skipped rather than raised on: this
    runs from cron, and one town whose article Wikipedia cannot pin down is not
    a reason for the account to say nothing today.
    """
    used = _recently_used_keys()
    materials = []
    for candidate in _candidates():
        if len(materials) >= PLACE_MAX_PER_RUN:
            break
        if candidate["key"] in used:
            continue
        try:
            material = _material(candidate)
        except Exception as exc:
            logger.warning("Places: %s failed (%s)", candidate["place"], exc)
            continue
        if material:
            materials.append(material)

    logger.info("Places: %s postable materials", len(materials))
    return materials
