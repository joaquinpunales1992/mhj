"""Metered access to property detail.

Three tiers, all configurable in settings:

    anonymous    VIEW_LIMIT_ANONYMOUS (5) properties, then the detail locks
    free account VIEW_LIMIT_FREE (50) properties, then it locks
    pro          unlimited

Only the *detail* locks — photos, price, floor plan and location always render,
so every property page stays useful to a first-time visitor and keeps its
value in search results. See `LOCKED_FIELDS` for exactly what's withheld.

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

# Property fields withheld once the meter is spent. Photos, price, floor plan
# and location are deliberately absent — they stay visible to everyone.
LOCKED_FIELDS = (
    "description",
    "land_area",
    "land_rights",
    "zoning",
    "city_planning",
    "building_structure",
    "construction_date",
    "traffic",
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

        locked     bool  — withhold LOCKED_FIELDS and show the wall
        tier       str   — anonymous | free | pro
        viewed     int   — properties opened so far (after this one)
        limit      int   — allowance for the tier, None when unlimited
        remaining  int   — views left, None when unlimited
        next_step  str   — 'signup' or 'upgrade', which wall to show
    """
    tier = tier_for(request)
    limit = limit_for(tier)

    if limit is None or is_crawler(request):
        return {
            "locked": False,
            "tier": tier,
            "viewed": 0,
            "limit": None,
            "remaining": None,
            "next_step": None,
        }

    seen = _seen_ids(request)
    already_seen = property_id in seen

    # Re-opening a property never costs a view, and never locks: if they could
    # read it once, taking it away later is just annoying.
    if already_seen:
        return {
            "locked": False,
            "tier": tier,
            "viewed": len(seen),
            "limit": limit,
            "remaining": max(0, limit - len(seen)),
            "next_step": None,
        }

    if len(seen) >= limit:
        return {
            "locked": True,
            "tier": tier,
            "viewed": len(seen),
            "limit": limit,
            "remaining": 0,
            "next_step": "signup" if tier == TIER_ANONYMOUS else "upgrade",
        }

    _remember(request, property_id)
    viewed = len(seen) + 1
    return {
        "locked": False,
        "tier": tier,
        "viewed": viewed,
        "limit": limit,
        "remaining": max(0, limit - viewed),
        "next_step": None,
    }
