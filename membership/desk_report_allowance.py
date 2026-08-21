"""How many desk reports a Pro member may claim, and whether they may now.

Three a month, renewing. Pro is US$10 a month and each report is an hour of
somebody's time plus a call to a Japanese municipal office, so this is worth
being clear-eyed about: three a month is more human work than the subscription
price covers on its own. It is justified by what the request means rather than by
what it costs — somebody asking us to research a specific house is the clearest
buying signal on the site, and the referral that may follow is worth orders of
magnitude more than the margin on a month of Pro.

The month is a rolling 30 days, not a calendar month. A calendar month lets
someone take three on the 31st and three more on the 1st — six reports in two
days, which is the queue this limit exists to prevent — and it makes the message
on the page harder to phrase than it needs to be.

Two deliberate generosities: a declined request returns the allowance, so nobody
loses a report to a listing we could not work on; and asking twice about the same
house is refused without spending anything.
"""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

# Defaults live here rather than in settings so the reasoning above travels with
# the numbers; settings can still override both.
DEFAULT_PER_MONTH = 3
DEFAULT_WINDOW_DAYS = 30


def _per_month():
    return getattr(settings, "DESK_REPORT_PRO_ALLOWANCE", DEFAULT_PER_MONTH)


def _window_days():
    return getattr(settings, "DESK_REPORT_WINDOW_DAYS", DEFAULT_WINDOW_DAYS)


def allowance_for(user):
    """What this user may do, as a dict the template renders directly.

        is_pro          bool  — an active subscription
        per_month       int   — reports included each month
        used            int   — claimed inside the current window
        remaining       int
        can_claim       bool
        blocked_by      str   — 'not_pro' | 'exhausted' | None
        next_claim_at   datetime|None — when the oldest claim leaves the window
        days_until_next int|None

    `blocked_by` exists so the page can say the true reason rather than a generic
    "not available", and so a member who has simply used this month's three is
    told when the next one arrives instead of being told no.
    """
    from membership.metering import user_is_pro
    from membership.models import DeskReportRequest

    per_month = _per_month()
    result = {
        "is_pro": False,
        "per_month": per_month,
        # Kept as `total` too: several templates read it, and the number they
        # want is the same one.
        "total": per_month,
        "used": 0,
        "remaining": 0,
        "can_claim": False,
        "blocked_by": "not_pro",
        "next_claim_at": None,
        "days_until_next": None,
        "window_days": _window_days(),
    }

    if not (user and user.is_authenticated):
        return result

    result["is_pro"] = user_is_pro(user)
    if not result["is_pro"]:
        return result

    window_start = timezone.now() - timedelta(days=_window_days())
    in_window = DeskReportRequest.objects.filter(
        user=user,
        status__in=DeskReportRequest.COUNTED_STATUSES,
        created_at__gte=window_start,
    ).order_by("created_at")

    used = in_window.count()
    result["used"] = used
    result["remaining"] = max(0, per_month - used)

    if result["remaining"] > 0:
        result["can_claim"] = True
        result["blocked_by"] = None
        return result

    # Out for now. The next one frees up when the oldest claim in the window
    # ages out of it, which is a date we can state rather than a vague "later".
    result["blocked_by"] = "exhausted"
    oldest = in_window.first()
    if oldest:
        next_at = oldest.created_at + timedelta(days=_window_days())
        result["next_claim_at"] = next_at
        result["days_until_next"] = max(1, (next_at - timezone.now()).days + 1)
    return result


def claim_error(user, listing):
    """Why this claim must be refused, or None if it may proceed.

    Checked server-side at the moment of claiming rather than trusted from the
    page: the button's state was decided when the page was rendered, which may
    have been yesterday, and two tabs can both show it enabled.
    """
    from membership.models import DeskReportRequest

    allowance = allowance_for(user)

    if not allowance["is_pro"]:
        return "Desk reports are included with Pro."
    if allowance["blocked_by"] == "exhausted":
        days = allowance["days_until_next"]
        when = (f" Your next one is available in {days} day"
                f"{'' if days == 1 else 's'}." if days else "")
        return (
            f"You have used all {allowance['per_month']} reports for this month."
            + when
        )

    if listing is not None:
        already = DeskReportRequest.objects.filter(
            user=user, listing=listing,
            status__in=DeskReportRequest.COUNTED_STATUSES,
        ).exists()
        if already:
            # Not an allowance problem, and it must not cost them a report.
            return "You have already asked for a report on this property."

    return None
