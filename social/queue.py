"""Which property goes out next, on any channel.

Its own module because of what importing it used to cost. This is a database
query and a sort — no video, no images, no numpy — but it lived in social/utils
beside the reel encoder, so the TikTok page could not ask "what is next?" without
loading moviepy into a web worker. That took the site down with a 503.

social.utils re-exports it, so nothing that already imported it from there had to
change.
"""

import logging
from datetime import timedelta

from django.db.models import Max
from django.utils import timezone

from inventory.models import Property
from social.constants import POST_ONLY_FEATURED, REPOST_COOLDOWN_DAYS

logger = logging.getLogger(__name__)


def select_properties_to_post(posts_queryset, price_limit, limit=None):
    """Properties with images under price_limit, cheapest first.

    Eligibility matches the homepage grid (show_in_front=True, price in
    (0, price_limit]) plus the social-only requirement of at least one image —
    we can't build a reel/post without a photo — and, while POST_ONLY_FEATURED
    is on, the featured flag. The flag is the shortlist: nothing goes out unless
    somebody marked it in the admin.

    That makes the size of the shortlist the thing to watch. The queue rotates
    through what is flagged and nothing else, so one flagged listing is not a
    queue, it is the same post every run.

    ORDERING. Featured and cheap are the priority, and they outrank posting
    history: a cheap featured listing leads the queue again after it has been
    posted, rather than waiting behind everything that has not. The one thing
    that displaces it is the cooldown — a listing posted within the last
    REPOST_COOLDOWN_DAYS drops behind everything that is not, so being the
    cheapest cannot mean going out twice in a week.

    Off cooldown:  featured first, then cheapest, then least-recently-posted.
    On cooldown:   least-recently-posted first, then featured, then cheapest.

    The second line matters when the shortlist is small enough that everything
    is cooling at once: with nothing eligible on merit, the queue falls back to
    whoever has waited longest rather than posting the cheapest one again.

    The history keys are timestamps (0.0 for never posted), not datetimes, so
    that never-posted and long-ago-posted listings sort against each other in
    the same group. Comparing a datetime with 0 raises, and the previous key
    only avoided it because posted and never-posted were never in the same
    group.

    This ordering has been reversed twice, so the reasoning is worth keeping:
    featured was once the FIRST key with no cooldown, and one featured 1400万
    listing was the head of the queue on every run — after it had already been
    posted — ahead of 1,558 never-posted properties. The cooldown is what makes
    "prioritise featured and cheap even if already posted" survivable; without
    it that instruction and the old bug are the same thing.

    `posts_queryset` is the SocialPost rows for the relevant channel; matching
    is by property_url == Property.url (same value written when a post is made).
    """
    rows = posts_queryset.values("property_url").annotate(last=Max("datetime"))
    last_posted = {r["property_url"]: r["last"] for r in rows}

    eligible = Property.objects.filter(
        show_in_front=True,
        images__isnull=False,
        price__gt=0,
        price__lte=price_limit,
    )
    if POST_ONLY_FEATURED:
        eligible = eligible.filter(featured=True)
    candidates = list(eligible.distinct())

    # The queue can only rotate through what is flagged. Below the batch size it
    # is not a rotation, it is the same listing going out again — which is worth
    # a line in the log rather than a quiet repeat nobody connects to the admin.
    if POST_ONLY_FEATURED and len(candidates) < (limit or 1) + 1:
        logger.warning(
            "Only %s featured listing(s) are eligible to post. The queue will "
            "repeat them until more are marked featured in the admin.",
            len(candidates),
        )

    cooldown_starts = (
        timezone.now() - timedelta(days=REPOST_COOLDOWN_DAYS)
    ).timestamp()

    def posted_at(property):
        """When this last went out, as a timestamp. 0.0 if it never has."""
        when = last_posted.get(property.url)
        return when.timestamp() if when else 0.0

    def rank(property):
        when = posted_at(property)
        if when > cooldown_starts:
            # Cooling: wait your turn, longest-waiting first.
            return (1, when, not property.featured, property.price or 0)
        # Eligible on merit: featured, then cheapest, then longest-waiting.
        return (0, not property.featured, property.price or 0, when)

    candidates.sort(key=rank)
    return candidates[:limit] if limit else candidates
