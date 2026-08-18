"""Confirmation mail for a paid consultation, with a calendar invite.

Two messages go out per booking: one to the person who paid, one to whoever runs
the calls. Both carry the same .ics attachment, because a time agreed in an email
body is a time somebody has to re-enter by hand, and that is where a booking gets
lost.

Times are written in the visitor's own zone and the agent's, side by side. A call
between Japan and Europe is on different calendar days at both ends, so stating
one zone only guarantees somebody misses it.
"""

import logging
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

logger = logging.getLogger(__name__)

ICS_STAMP = "%Y%m%dT%H%M%SZ"


def _zone(name, fallback="UTC"):
    try:
        return ZoneInfo(name or fallback)
    except Exception:
        return ZoneInfo(fallback)


def _fmt(dt, zone_name):
    """'Thu 20 Aug 2026, 10:00 (Asia/Tokyo)'."""
    local = dt.astimezone(_zone(zone_name))
    return f"{local:%a %d %b %Y, %H:%M} ({zone_name})"


def _escape_ics(text):
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def build_ics(booking):
    """A minimal, valid VEVENT.

    UTC throughout (the trailing Z), which avoids shipping a VTIMEZONE block and
    is unambiguous in every client. UID is stable per booking so a re-sent
    invitation updates the existing entry instead of creating a second one.
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//My Akiya in Japan//Consultation//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:consultation-{booking.pk}@akiyainjapan.com",
        f"DTSTAMP:{timezone.now():{ICS_STAMP}}",
        f"DTSTART:{booking.starts_at:{ICS_STAMP}}",
        f"DTEND:{booking.ends_at:{ICS_STAMP}}",
        f"SUMMARY:{_escape_ics('Akiya consultation — My Akiya in Japan')}",
        f"DESCRIPTION:{_escape_ics(_description(booking))}",
        "STATUS:CONFIRMED",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    # CRLF, not \n: RFC 5545 requires it, and some clients reject the file
    # outright without it.
    return "\r\n".join(lines) + "\r\n"


def _description(booking):
    parts = [f"{booking.duration_minutes}-minute consultation with My Akiya in Japan."]
    if booking.listing:
        parts.append(
            f"About: {booking.listing.get_title_for_front()} — "
            f"{booking.listing.get_location_for_front()}"
        )
        parts.append(f"https://akiyainjapan.com{booking.listing.get_public_url}")
    if booking.notes:
        parts.append(f"Their notes: {booking.notes}")
    parts.append("We will send the call link before the call.")
    return "\n".join(parts)


def _visitor_body(booking):
    visitor_zone = booking.visitor_timezone or "UTC"
    lines = [
        f"Hello {booking.name},",
        "",
        "Your consultation is booked and paid — thank you.",
        "",
        f"  When:     {_fmt(booking.starts_at, visitor_zone)}",
        f"            {_fmt(booking.starts_at, settings.CONSULT_TIMEZONE)}",
        f"  Length:   {booking.duration_minutes} minutes",
        f"  Paid:     {booking.amount} {booking.currency}",
        f"  Ref:      #{booking.pk}",
    ]
    if booking.listing:
        lines += [
            "",
            "We will come to the call having looked at:",
            f"  {booking.listing.get_title_for_front()} — "
            f"{booking.listing.get_location_for_front()}",
            f"  https://akiyainjapan.com{booking.listing.get_public_url}",
        ]
    lines += [
        "",
        "The calendar invitation is attached, and we will email the call link "
        "before we speak.",
        "",
        "Need to move it or can't make it? Reply to this email and we will sort "
        "it out — no charge for rescheduling.",
        "",
        "See you then,",
        "My Akiya in Japan",
    ]
    return "\n".join(lines)


def _owner_body(booking):
    visitor_zone = booking.visitor_timezone or "UTC"
    lines = [
        f"Paid consultation booked — #{booking.pk}",
        "",
        f"  Who:      {booking.name} <{booking.email}>",
        f"  When:     {_fmt(booking.starts_at, settings.CONSULT_TIMEZONE)}",
        f"            {_fmt(booking.starts_at, visitor_zone)} — their time",
        f"  Length:   {booking.duration_minutes} minutes",
        f"  Paid:     {booking.amount} {booking.currency}",
        f"  PayPal:   order {booking.paypal_order_id} / capture {booking.paypal_capture_id}",
    ]
    if booking.listing:
        lines += [
            "",
            "  Property: "
            f"{booking.listing.get_title_for_front()} — "
            f"{booking.listing.get_location_for_front()} — "
            f"{booking.listing.get_price_for_front}",
            f"            https://akiyainjapan.com{booking.listing.get_public_url}",
        ]
    if booking.notes:
        lines += ["", "  Their notes:", f"    {booking.notes}"]
    lines += ["", "Send them the call link."]
    return "\n".join(lines)


def send_confirmation(booking):
    """Send both messages. Returns how many were accepted.

    Each is sent independently: the payer's confirmation is the one that must not
    be lost to a failure in ours.
    """
    ics = build_ics(booking)
    visitor_zone = booking.visitor_timezone or "UTC"
    local = booking.starts_at.astimezone(_zone(visitor_zone))
    sent = 0

    for to, subject, body in (
        (
            [booking.email],
            f"Your akiya consultation — {local:%a %d %b, %H:%M}",
            _visitor_body(booking),
        ),
        (
            [settings.CONSULT_NOTIFY_EMAIL],
            f"Paid consultation #{booking.pk} — {booking.name}",
            _owner_body(booking),
        ),
    ):
        try:
            message = EmailMessage(
                subject=subject,
                body=body,
                to=to,
                reply_to=[settings.CONSULT_NOTIFY_EMAIL],
            )
            # Mimetype without charset: EmailMessage appends its own, and
            # passing one here produced a duplicated
            # `charset="utf-8"; charset="utf-8"` in the part header.
            message.attach("consultation.ics", ics, "text/calendar")
            sent += message.send(fail_silently=False)
        except Exception as e:
            logger.error("Consultation %s: could not email %s: %s", booking.pk, to, e)
    return sent
