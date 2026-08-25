"""Deciding what the account says today, and in which medium.

This is the part that was missing. The bot had one thing to say — here is a
house — because the only thing that could produce a post was a Property. Now
several sources offer material, and something has to choose between them.

It chooses by weight rather than by schedule: a base weight per format, scaled
by how that format has actually performed, penalised for repeating the last
thing posted, and filtered by what has been said recently. A schedule would
have to be maintained; this only has to be nudged.
"""

import logging
import random
from datetime import timedelta

from django.utils import timezone

from social.constants import CONTENT_WEIGHTS
from social.content.sources import gather_all
from social.models import ContentDraft, SocialPost

logger = logging.getLogger(__name__)

# A format needs this many posts before its numbers mean anything. Below it,
# every format is treated as average — otherwise one lucky reel decides the
# editorial line for a month.
MIN_POSTS_FOR_SIGNAL = 3

# How far performance is allowed to move a weight. Uncapped, a single viral post
# would crowd everything else out of the feed permanently.
PERFORMANCE_FLOOR = 0.5
PERFORMANCE_CEILING = 2.0

# Posting the same format twice running is what makes an account feel like a
# script. Not forbidden — sometimes it is the right call — just discouraged.
REPEAT_PENALTY = 0.35

FEED_MEDIA = ("post", "reel")


def performance_multipliers(days=90):
    """How each format has actually done, relative to the average.

    Reads the insights snapshot that `manage.py reel_insights` writes. Posts
    that have never had their insights fetched are skipped rather than counted
    as zero — an unmeasured post is not a failed one.
    """
    since = timezone.now() - timedelta(days=days)
    posts = SocialPost.objects.filter(
        datetime__gte=since, views__isnull=False
    ).values_list("post_kind", "views")

    by_kind = {}
    for kind, views in posts:
        by_kind.setdefault(kind, []).append(views)

    measured = {
        kind: sum(values) / len(values)
        for kind, values in by_kind.items()
        if len(values) >= MIN_POSTS_FOR_SIGNAL
    }
    if len(measured) < 2:
        return {}

    average = sum(measured.values()) / len(measured)
    if not average:
        return {}

    return {
        kind: max(PERFORMANCE_FLOOR, min(PERFORMANCE_CEILING, value / average))
        for kind, value in measured.items()
    }


def _recent_keys():
    """Keys posted recently enough to still count as recently said.

    Returns {key: datetime of the most recent use}. Cooldowns are compared
    against this per material, because 'too soon' means something different for
    a news story (ever) and for the weekly listings count (seven days).
    """
    used = {}
    for key, posted_at, created_at in ContentDraft.objects.values_list(
        "key", "posted_at", "created_at"
    ):
        when = posted_at or created_at
        if not when:
            continue
        if key not in used or when > used[key]:
            used[key] = when
    return used


def _off_cooldown(material, used, now):
    last = used.get(material.key)
    if last is None:
        return True
    if material.cooldown_days is None:
        return False  # said once, never again — news stories, mainly
    if material.cooldown_days == 0:
        return True   # no cooldown of its own; its source manages the queue
    return last < now - timedelta(days=material.cooldown_days)


def _posted_to_feed_today():
    start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return SocialPost.objects.filter(
        datetime__gte=start, content_type__in=FEED_MEDIA
    ).exists()


def _last_kind():
    last = SocialPost.objects.order_by("-datetime").first()
    return last.post_kind if last else None


def choose(materials, prefer_story=None):
    """Pick one material and the medium to say it in, or (None, None).

    `prefer_story` exists so the caller can override the once-a-day feed rule;
    left alone, the feed gets one post a day and anything else that comes up
    goes out as a story. That is roughly how a person runs an account: one
    thing worth the grid, several worth a tap.
    """
    if not materials:
        return None, None

    now = timezone.now()
    used = _recent_keys()
    candidates = [m for m in materials if _off_cooldown(m, used, now)]
    if not candidates:
        logger.info("Every available material is inside its cooldown.")
        return None, None

    if prefer_story is None:
        prefer_story = _posted_to_feed_today()

    if prefer_story:
        # A reel cannot be turned into a story card — it is a video the reel
        # pipeline builds for the feed — so drop those from a story slot rather
        # than posting a second listing to the grid.
        candidates = [m for m in candidates if m.medium != "reel"]
        if not candidates:
            logger.info("Nothing suitable for a story slot today.")
            return None, None

    multipliers = performance_multipliers()
    last_kind = _last_kind()

    weights = []
    for material in candidates:
        weight = CONTENT_WEIGHTS.get(material.kind, 1.0) * material.weight
        weight *= multipliers.get(material.kind, 1.0)
        if material.kind == last_kind:
            weight *= REPEAT_PENALTY
        weights.append(max(weight, 0.01))

    chosen = random.choices(candidates, weights=weights, k=1)[0]
    medium = "story" if prefer_story else chosen.medium
    logger.info(
        "Planner chose %s as a %s (from %s candidates)", chosen, medium,
        len(candidates),
    )
    return chosen, medium


def plan(prefer_story=None):
    """Gather from every source and pick. Returns (material, medium)."""
    return choose(gather_all(), prefer_story=prefer_story)
