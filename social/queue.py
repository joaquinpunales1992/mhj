"""Which property goes out next, on any channel.

Its own module because of what importing it used to cost. This is a database
query and a sort — no video, no images, no numpy — but it lived in social/utils
beside the reel encoder, so the TikTok page could not ask "what is next?" without
loading moviepy into a web worker. That took the site down with a 503.

social.utils re-exports it, so nothing that already imported it from there had to
change.
"""

from django.db.models import Max

from inventory.models import Property


def select_properties_to_post(posts_queryset, price_limit, limit=None):
    """Properties with images under price_limit, cheapest first.

    Eligibility matches the homepage grid (show_in_front=True, price in
    (0, price_limit]) plus the social-only requirement of at least one image
    — we can't build a reel/post without a photo. Featured is NOT required;
    it's only a tiebreaker.

    Ordering: never-posted properties get a turn before anything is reposted,
    then featured ones, then the least-recently-posted (to keep the rotation
    fair), then cheapest first — which, since everything never posted has the
    same empty posting history, is what actually decides the queue day to day.

    Featured used to be the first key, on the reasoning that the social feed
    should mirror the home page grid. On the home page that ordering costs
    nothing: every listing is on the page, featured ones are simply at the top.
    A queue is not a page. One featured 1400万 listing sat at the head of it
    ahead of 1,558 never-posted properties, kept sitting there after it had been
    posted, and would have been next on every run for the rest of the year.
    Featured now boosts a listing exactly once. It cannot repeat, because the
    first key puts every never-posted listing ahead of anything already posted —
    which is what the old featured-first ordering lacked, not a price cap.

    `posts_queryset` is the SocialPost rows for the relevant channel; matching
    is by property_url == Property.url (same value written when a post is made).
    """
    rows = posts_queryset.values("property_url").annotate(last=Max("datetime"))
    last_posted = {r["property_url"]: r["last"] for r in rows}

    candidates = list(
        Property.objects.filter(
            show_in_front=True,
            images__isnull=False,
            price__gt=0,
            price__lte=price_limit,
        ).distinct()
    )
    # Sort key, in priority order:
    #   1. already-posted?    never-posted ahead of posted, so the boost below
    #                         is worth exactly one turn and cannot repeat
    #   2. not featured       a featured listing leads its group, once
    #   3. last-posted-time   oldest repost first (only separates the posted
    #                         group — everything never posted ties at 0 here)
    #   4. price              cheapest first
    candidates.sort(
        key=lambda p: (
            last_posted.get(p.url) is not None,
            not p.featured,
            last_posted.get(p.url) or 0,
            p.price or 0,
        )
    )
    return candidates[:limit] if limit else candidates
