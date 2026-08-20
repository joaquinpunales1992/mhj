"""Reading back how a post did, so posting stops being guesswork.

The bot has always been able to publish and never able to tell whether anything
worked. Captions rotate through six creative angles and a folder of soundtracks
at random, and until now nothing recorded which combination went further —
so every change to the format was an opinion.

This module is the other half of that loop: given the published media id (now
stored on SocialPost), ask the Graph API what happened and keep the numbers
beside the post that produced them.

Deliberately kept free of moviepy and the posting code so it can be imported —
and tested — without pulling in the video encoder.

Metric names are the awkward part. Meta renames them between versions:
`plays` became `views`, and `ig_reels_avg_watch_time` only exists for reels.
Rather than pinning to one spelling and silently reporting nothing when the
version moves, an unsupported metric is dropped from the request and the rest
are fetched — partial numbers beat none.
"""

import logging
import re

import requests
from django.utils import timezone

from social.constants import GRAPH_API_VERSION

logger = logging.getLogger(__name__)

# Requested in this order; whatever the API refuses is dropped and the rest
# still come back. `views` and `plays` are the same fact under two names, so
# both are asked for and land in the same column.
REEL_METRICS = [
    "reach",
    "views",
    "plays",
    "likes",
    "comments",
    "saved",
    "shares",
    "total_interactions",
    "ig_reels_avg_watch_time",
]

# Carousels and single images have no watch time and no plays.
POST_METRICS = [
    "reach",
    "views",
    "likes",
    "comments",
    "saved",
    "shares",
    "total_interactions",
]

# API metric name -> SocialPost field. Two names map onto `views` on purpose.
FIELD_BY_METRIC = {
    "reach": "reach",
    "views": "views",
    "plays": "views",
    "likes": "likes",
    "comments": "comments_count",
    "saved": "saves",
    "shares": "shares",
    "total_interactions": "total_interactions",
    "ig_reels_avg_watch_time": "avg_watch_time_ms",
}

# A request is retried at most this many times while dropping metrics the API
# rejects. Bounded so a permanently unhappy endpoint cannot loop.
MAX_METRIC_RETRIES = 3

# Errors that have nothing to do with which metrics were asked for. Retrying
# these while dropping metrics is pure waste — and worse, it buries the real
# reason (a missing permission, an expired token) under a generic "no numbers
# returned". Code 10 is the one that bites here: publishing works with
# instagram_content_publish, but reading insights needs
# instagram_manage_insights, which is granted separately.
NON_METRIC_ERROR_CODES = {
    10,   # application does not have permission for this action
    190,  # access token expired/invalid
    102,  # session invalid
    104,  # missing access token
    200,  # permissions error
    3,    # unsupported operation on this object
}


def _metric_value(entry):
    """Pull the number out of one insights entry.

    Insights come back as {"name": ..., "values": [{"value": N}]} for lifetime
    metrics and occasionally as a bare {"name": ..., "total_value": {"value": N}}
    for the newer ones. Both shapes appear depending on version, so both are
    read rather than assuming whichever one this account happens to return.
    """
    values = entry.get("values")
    if isinstance(values, list) and values:
        value = values[0].get("value")
        if isinstance(value, (int, float)):
            return int(value)
    total = entry.get("total_value")
    if isinstance(total, dict) and isinstance(total.get("value"), (int, float)):
        return int(total["value"])
    return None


def _unsupported_metrics(error_message, requested):
    """Which of the metrics we asked for did this error object to?

    Meta usually names the position rather than the metric — "(#100) metric[2]
    must be one of the following values: ..." — and then lists the values it
    *does* accept. Reading names out of the whole message would therefore drop
    perfectly good metrics, because the allowed list is full of them. So:
    positions first, then names quoted before that allowed list, then the two
    that are known to move between versions.
    """
    lowered = (error_message or "").lower()

    indices = {int(i) for i in re.findall(r"metric\[(\d+)\]", lowered)}
    by_index = [m for i, m in enumerate(requested) if i in indices]
    if by_index:
        return by_index

    # Everything after "following values:" is what Meta accepts, not what it
    # refused — only the text before it can name the offender.
    head = lowered.split("following values:")[0]
    by_name = [m for m in requested if m in head]
    if by_name:
        return by_name

    # Nothing identifiable. Drop the ones that move between versions and retry;
    # if the request still fails, the caller gives up rather than guessing on.
    return [m for m in ("views", "plays", "ig_reels_avg_watch_time") if m in requested]


def fetch_media_insights(media_id, token, metrics=None, problems=None):
    """Return {metric: value} for one published media, or {} if nothing came back.

    Never raises. A post whose insights cannot be read is not a reason to abort
    a refresh over the rest of them.

    `problems`, when given, collects the reason each refusal happened, so the
    caller can tell the operator what to fix instead of only reporting that
    nothing arrived.
    """
    remaining = list(metrics or REEL_METRICS)
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{media_id}/insights"

    for _ in range(MAX_METRIC_RETRIES):
        if not remaining:
            return {}
        try:
            response = requests.get(
                url,
                params={"metric": ",".join(remaining), "access_token": token},
                timeout=30,
            )
        except requests.RequestException as e:
            logger.warning("Insights request failed for %s: %s", media_id, e)
            return {}

        if response.status_code == 200:
            data = response.json().get("data", [])
            values = {}
            for entry in data:
                value = _metric_value(entry)
                if value is not None:
                    values[entry.get("name")] = value
            return values

        message = ""
        code = None
        try:
            error = response.json().get("error", {}) or {}
            message = error.get("message", "")
            code = error.get("code")
        except ValueError:
            message = response.text

        # A permission or token problem is not a metric problem: dropping
        # metrics and asking again cannot fix it, and the retry hides the one
        # piece of information worth surfacing.
        if code in NON_METRIC_ERROR_CODES:
            logger.warning("Insights for %s refused: %s", media_id, message)
            if problems is not None:
                problems.append(message)
            return {}

        refused = _unsupported_metrics(message, remaining)
        if not refused:
            logger.warning(
                "Insights for %s refused (%s): %s", media_id, response.status_code,
                message,
            )
            if problems is not None:
                problems.append(message)
            return {}
        logger.info(
            "Dropping unsupported metric(s) %s for %s and retrying",
            ", ".join(refused), media_id,
        )
        remaining = [m for m in remaining if m not in refused]

    return {}


def refresh_post_insights(post, token, problems=None):
    """Fetch and store the snapshot for one SocialPost. True if numbers landed."""
    if not post.media_id:
        return False

    metrics = REEL_METRICS if post.content_type == "reel" else POST_METRICS
    values = fetch_media_insights(post.media_id, token, metrics, problems)
    if not values:
        return False

    updated = ["insights_fetched_at"]
    for metric, value in values.items():
        field = FIELD_BY_METRIC.get(metric)
        # `views` and `plays` share a column: whichever arrives first wins, and
        # the second is ignored rather than overwriting it with the same fact.
        if not field or field in updated:
            continue
        setattr(post, field, value)
        updated.append(field)

    post.insights_fetched_at = timezone.now()
    post.save(update_fields=updated)
    return True


def refresh_insights(posts, token):
    """Refresh a queryset of posts.

    Returns (fetched, skipped, problems) — the distinct refusal messages, most
    common first. One missing permission refuses every post identically, so the
    caller prints the reason once rather than 900 times.
    """
    fetched = skipped = 0
    problems = []
    for post in posts:
        if refresh_post_insights(post, token, problems):
            fetched += 1
        else:
            skipped += 1

    seen = {}
    for message in problems:
        seen[message] = seen.get(message, 0) + 1
    ranked = sorted(seen.items(), key=lambda pair: pair[1], reverse=True)
    return fetched, skipped, ranked


def group_by(posts, key):
    """Average the numbers that matter, grouped by an attribute of the post.

    Posts with no insights are excluded from the averages instead of counting as
    zero — an unfetched post would otherwise drag a good angle's average down
    and make the comparison worse than no comparison.
    """
    buckets = {}
    for post in posts:
        if post.insights_fetched_at is None:
            continue
        label = getattr(post, key, "") or "(none)"
        buckets.setdefault(label, []).append(post)

    rows = []
    for label, group in buckets.items():
        def mean(attr):
            numbers = [getattr(p, attr) for p in group if getattr(p, attr) is not None]
            return sum(numbers) / len(numbers) if numbers else None

        rows.append(
            {
                "label": label,
                "posts": len(group),
                "views": mean("views"),
                "reach": mean("reach"),
                "saves": mean("saves"),
                "shares": mean("shares"),
                "interactions": mean("total_interactions"),
                "watch_ms": mean("avg_watch_time_ms"),
            }
        )
    # Best first, on views — the number that reflects how far the post travelled
    # rather than how much the people who already follow you liked it.
    rows.sort(key=lambda r: (r["views"] or 0), reverse=True)
    return rows
