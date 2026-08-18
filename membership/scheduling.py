"""Which consultation slots are bookable, and when.

Availability is defined in the agent's own timezone (they take these calls from
Japan) and converted for whoever is looking. Everything crossing a boundary —
the database, the PayPal order, the ICS file — is UTC; the visitor's zone is used
only for display and stored on the booking so we can show them the time they
actually agreed to.

The rules, all configurable in settings:

    CONSULT_WEEKDAYS         which days are bookable at all
    CONSULT_OPEN / _CLOSE    daily window, in CONSULT_TIMEZONE
    CONSULT_DURATION_MINUTES how long a call runs
    CONSULT_SLOT_STEP_MINUTES the grid slots start on
    CONSULT_LEAD_HOURS       minimum notice
    CONSULT_HORIZON_DAYS     how far ahead the calendar opens
    CONSULT_HOLD_MINUTES     how long an unpaid hold blocks a slot

A slot must finish inside the daily window, so a 60-minute call in a 10:00-18:00
window has its last start at 17:00, not 18:00.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Q
from django.utils import timezone


def agent_zone():
    return ZoneInfo(settings.CONSULT_TIMEZONE)


def _parse_hhmm(value, fallback):
    try:
        hour, _, minute = str(value).partition(":")
        return time(int(hour), int(minute or 0))
    except (TypeError, ValueError):
        return fallback


def window():
    """(open, close) as times in the agent's zone."""
    return (
        _parse_hhmm(settings.CONSULT_OPEN, time(10, 0)),
        _parse_hhmm(settings.CONSULT_CLOSE, time(18, 0)),
    )


def taken_slots():
    """Start times (UTC) that are already occupied.

    An expired hold does not occupy anything — the visitor walked away from
    checkout — so it is filtered out here rather than being deleted, which keeps
    the abandoned attempts visible in the admin.
    """
    from membership.models import Consultation

    now = timezone.now()
    live = Consultation.objects.filter(
        status__in=Consultation.BLOCKING_STATUSES
    ).exclude(
        Q(status=Consultation.STATUS_HOLD) & Q(hold_expires_at__lte=now)
    )
    return set(live.values_list("starts_at", flat=True))


def available_slots(now=None):
    """Every bookable slot start, in UTC, ordered.

    Generated in the agent's local time so the window means what it says on each
    calendar day, then converted — the alternative (stepping through UTC and
    converting to check the window) silently shifts the window by an hour in any
    zone that observes DST.
    """
    now = now or timezone.now()
    zone = agent_zone()
    open_at, close_at = window()
    duration = timedelta(minutes=settings.CONSULT_DURATION_MINUTES)
    step = timedelta(minutes=settings.CONSULT_SLOT_STEP_MINUTES)
    earliest = now + timedelta(hours=settings.CONSULT_LEAD_HOURS)
    latest = now + timedelta(days=settings.CONSULT_HORIZON_DAYS)
    taken = taken_slots()

    slots = []
    day = now.astimezone(zone).date()
    end_day = latest.astimezone(zone).date()
    while day <= end_day:
        if day.weekday() in set(settings.CONSULT_WEEKDAYS):
            cursor = datetime.combine(day, open_at, tzinfo=zone)
            day_close = datetime.combine(day, close_at, tzinfo=zone)
            while cursor + duration <= day_close:
                start_utc = cursor.astimezone(ZoneInfo("UTC"))
                if earliest <= start_utc <= latest and start_utc not in taken:
                    slots.append(start_utc)
                cursor += step
        day += timedelta(days=1)
    return slots


def is_available(start_utc, now=None):
    """Whether this exact start is still bookable.

    Re-checked at booking time: the visitor's page may have been open for an
    hour, and the slot they clicked can have been taken or fallen inside the
    lead time since. This is a guard, not the guarantee — the guarantee is the
    unique constraint on the model.
    """
    if start_utc is None:
        return False
    return start_utc in set(available_slots(now=now))


def group_by_day(slots, display_zone):
    """[(date, [slots]), ...] grouped in the *viewer's* zone.

    Grouping has to happen in the zone the dates will be labelled in. A 10:00
    Tokyo slot is the previous evening in New York, so grouping in the agent's
    zone would file it under a date the viewer never sees.
    """
    try:
        zone = ZoneInfo(display_zone)
    except Exception:
        zone = ZoneInfo("UTC")

    days = {}
    for slot in slots:
        local = slot.astimezone(zone)
        days.setdefault(local.date(), []).append(slot)
    return [(day, days[day]) for day in sorted(days)]


def hold_expiry(now=None):
    now = now or timezone.now()
    return now + timedelta(minutes=settings.CONSULT_HOLD_MINUTES)
