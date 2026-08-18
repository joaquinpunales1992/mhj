"""Metered access to property detail.

Three tiers, all configurable in settings:

    anonymous    VIEW_LIMIT_ANONYMOUS (5) properties, open fields
    free account VIEW_LIMIT_FREE (25) properties, open fields
    pro          unlimited properties, open + premium fields

Two axes: how many properties a tier may open, and how much of each it may see.

Photos, title, price and location are never withheld from anyone — they are what
search engines index and what makes a property page worth arriving on. The two
areas and the description are open to every tier as well, but only on the
properties that tier may actually open: past the allowance they are withheld
along with the rest of the detail, which is what `locked` drives in the
template. Re-opening a property already seen never locks, so nothing is taken
away retroactively.

Two rules protect search traffic and must not be weakened:

  1. Crawlers are never metered and never see a wall. Serving Googlebot
     something different from a first-time human is only safe in this
     direction — the bot sees *more*, never less.
  2. A property already viewed never costs a second view, so re-opening a
     listing you're considering can't push you over the limit.
"""

import re

from django.conf import settings

SESSION_KEY = "viewed_property_ids"

# Matched case-insensitively against the User-Agent. Deliberately broad: the
# cost of mistaking a human for a crawler is one unmetered pageview, while the
# cost of metering Googlebot is losing the ranking that page depends on.
CRAWLER_PATTERN = re.compile(
    r"bot|crawl|spider|slurp|bingpreview|facebookexternalhit|embedly|"
    r"quora link preview|showyoubot|outbrain|pinterest|slackbot|vkshare|"
    r"w3c_validator|whatsapp|flipboard|tumblr|discordbot|telegrambot|"
    r"applebot|duckduckgo|yandex|baiduspider|semrush|ahrefs|lighthouse|"
    r"headlesschrome|google-inspectiontool|chrome-lighthouse",
    re.IGNORECASE,
)

# What each tier may see, independent of how many properties it may open.
#
#   OPEN     every tier, anonymous included, on every property that tier may
#            open. These are the facts a listing is useless without and the
#            text search engines rank the page on, so no tier is singled out —
#            but they are still subject to the allowance: on a new property
#            past the limit the template withholds them behind the wall.
#   PREMIUM  Pro only — the derived analysis that is the reason to subscribe.
#
# There was briefly a middle "standard" tier that held the areas back from
# anonymous visitors. It was wrong twice over: it contradicted the meter wall's
# own promise a few lines into contact_seller.html ("building area ... stays
# visible"), and because crawlers resolve to the anonymous tier it also stopped
# Googlebot from indexing the areas.
FIELDS_OPEN = (
    "photos",
    "title",
    "price",
    "location",
    "building_area",
    "land_area",
    "description",
)
FIELDS_PREMIUM = (
    "price_per_sqm",
    "short_term_rental_potential",
    "land_rights",
    "risk_information",
)

TIER_ANONYMOUS = "anonymous"
TIER_FREE = "free"
TIER_PRO = "pro"


def is_crawler(request):
    user_agent = request.META.get("HTTP_USER_AGENT", "")
    return bool(CRAWLER_PATTERN.search(user_agent))


def user_is_pro(user):
    """True when the account has an active paid subscription.

    Tolerates the subscription table not existing yet so metering works before
    billing is switched on.
    """
    if not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    subscription = getattr(user, "subscription", None)
    return bool(subscription and subscription.is_active)


def tier_for(request):
    if user_is_pro(request.user):
        return TIER_PRO
    return TIER_FREE if request.user.is_authenticated else TIER_ANONYMOUS


def limit_for(tier):
    if tier == TIER_PRO:
        return None  # unlimited
    if tier == TIER_FREE:
        # 0 means "no cap for members" — the switch for running free accounts
        # unlimited while still metering anonymous visitors.
        return settings.VIEW_LIMIT_FREE or None
    return settings.VIEW_LIMIT_ANONYMOUS or None


def _seen_ids(request):
    """Ids this visitor has already opened.

    Signed-in users get a persistent record so the allowance follows them
    across devices; anonymous visitors are tracked in the session only, which
    is the most we can honestly do without an account.
    """
    if request.user.is_authenticated:
        from membership.models import PropertyView

        return set(
            PropertyView.objects.filter(user=request.user).values_list(
                "property_id", flat=True
            )
        )
    return set(request.session.get(SESSION_KEY, []))


def _remember(request, property_id):
    if request.user.is_authenticated:
        from membership.models import PropertyView

        PropertyView.objects.get_or_create(
            user=request.user, property_id=property_id
        )
        return

    seen = request.session.get(SESSION_KEY, [])
    if property_id not in seen:
        seen.append(property_id)
        request.session[SESSION_KEY] = seen
        request.session.modified = True


def check_access(request, property_id):
    """Decide whether this property's detail is unlocked, recording the view.

    Returns a dict the template renders directly:

        locked     bool  — the allowance is spent; show the wall
        tier       str   — anonymous | free | pro
        viewed     int   — properties opened so far (after this one)
        limit      int   — allowance for the tier, None when unlimited
        remaining  int   — views left, None when unlimited
        next_step  str   — 'signup' or 'upgrade', which wall to show
        premium    bool  — may see FIELDS_PREMIUM (Pro only)

    Quota and field access are separate axes. Anonymous and free accounts differ
    only in how many properties they may open — both see every open field on the
    properties they do open. Pro adds the premium analysis.
    """
    tier = tier_for(request)
    limit = limit_for(tier)

    # `premium` is purely a tier question and never a quota one: a Pro
    # subscriber has no limit to run out of, and a free account that does run out
    # was never entitled to these fields anyway. The open fields are the ones the
    # allowance affects, and the template keys that off `locked`.
    fields = {
        "premium": tier == TIER_PRO,
    }

    def result(locked, viewed, remaining, next_step):
        return dict(
            locked=locked,
            tier=tier,
            viewed=viewed,
            limit=limit,
            remaining=remaining,
            next_step=next_step,
            **fields,
        )

    if limit is None or is_crawler(request):
        return result(False, 0, None, None)

    seen = _seen_ids(request)

    # Re-opening a property never costs a view, and never locks: if they could
    # read it once, taking it away later is just annoying.
    if property_id in seen:
        return result(False, len(seen), max(0, limit - len(seen)), None)

    if len(seen) >= limit:
        return result(
            True,
            len(seen),
            0,
            "signup" if tier == TIER_ANONYMOUS else "upgrade",
        )

    _remember(request, property_id)
    viewed = len(seen) + 1
    return result(False, viewed, max(0, limit - viewed), None)
