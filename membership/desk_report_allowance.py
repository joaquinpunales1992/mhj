"""How many desk reports a Pro member may claim, and whether they may now.

Two limits, and they answer different worries:

    one a month     paces the work. Each report is an hour of somebody's time and
                    a phone call to a Japanese municipal office, so three
                    arriving on a Tuesday is not a queue we can honour, and a
                    promise we cannot keep is worse than a smaller promise.

    three in total  is the value of the subscription, not a monthly renewable
                    allowance. Pro is US$10 a month; three reports is already
                    more human work than that pays for, and it is justified only
                    because someone asking us to research a specific house is the
                    highest-intent signal on the site.

Both are deliberately generous in one direction: a declined request gives the
allowance back, so nobody loses a report to a listing we could not work on.

The "month" is a rolling 30 days rather than a calendar month. A calendar month
lets someone claim on the 31st and again on the 1st, which is the opposite of
pacing, and it makes the message on the page ("available again in 6 days")
harder to explain than it needs to be.
"""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

# Defaults live here rather than in settings so the reasoning above travels with
# the numbers; settings can still override both.
DEFAULT_TOTAL = 3
DEFAULT_COOLDOWN_DAYS = 30


def _total_allowance():
    return getattr(settings, "DESK_REPORT_PRO_ALLOWANCE", DEFAULT_TOTAL)


def _cooldown_days():
    return getattr(settings, "DESK_REPORT_COOLDOWN_DAYS", DEFAULT_COOLDOWN_DAYS)


def allowance_for(user):
    """What this user may do, as a dict the template renders directly.

        is_pro          bool  — an active subscription
        total           int   — reports included with Pro
        used            int   — claimed and not declined
        remaining       int
        can_claim       bool  — Pro, has some left, and not inside the cooldown
        blocked_by      str   — 'not_pro' | 'exhausted' | 'cooldown' | None
        next_claim_at   datetime|None — when the cooldown lifts
        days_until_next int|None

    `blocked_by` exists so the page can say the true reason rather than a generic
    "not available": the three cases need three different messages, and one of
    them ("you have one waiting in 6 days") is good news.
    """
    from membership.metering import user_is_pro
    from membership.models import DeskReportRequest

    total = _total_allowance()
    result = {
        "is_pro": False,
        "total": total,
        "used": 0,
        "remaining": 0,
        "can_claim": False,
        "blocked_by": "not_pro",
        "next_claim_at": None,
        "days_until_next": None,
        "cooldown_days": _cooldown_days(),
    }

    if not (user and user.is_authenticated):
        return result

    result["is_pro"] = user_is_pro(user)
    if not result["is_pro"]:
        return result

    claims = DeskReportRequest.objects.filter(
        user=user, status__in=DeskReportRequest.COUNTED_STATUSES
    )
    used = claims.count()
    result["used"] = used
    result["remaining"] = max(0, total - used)

    if result["remaining"] == 0:
        result["blocked_by"] = "exhausted"
        return result

    latest = claims.order_by("-created_at").first()
    if latest:
        next_at = latest.created_at + timedelta(days=_cooldown_days())
        if next_at > timezone.now():
            result["blocked_by"] = "cooldown"
            result["next_claim_at"] = next_at
            result["days_until_next"] = max(
                1, (next_at - timezone.now()).days + 1
            )
            return result

    result["can_claim"] = True
    result["blocked_by"] = None
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
        return (
            f"You have used all {allowance['total']} of the reports included "
            "with Pro. Email us if you need another."
        )
    if allowance["blocked_by"] == "cooldown":
        days = allowance["days_until_next"]
        return (
            f"One report a month, so your next one is available in {days} "
            f"day{'' if days == 1 else 's'}."
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
