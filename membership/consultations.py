"""Booking a paid consultation: pick a slot, pay, get a confirmed call.

Three views, one for each step the visitor takes:

    book_consultation      POST a slot + contact details -> hold + PayPal order
    consultation_return    PayPal sends the payer back here -> capture -> booked
    consultation_cancelled PayPal's cancel_url -> release the hold

The ordering matters. The hold is written before the visitor leaves for PayPal so
the slot cannot be sold twice while they are in checkout, and the booking is only
marked paid after capture returns COMPLETED. Anything that fails in between
leaves a hold that expires on its own.
"""

import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, available_timezones

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from inventory.models import Property
from membership.models import Consultation
from membership.paypal_orders import PayPalError, capture_order, create_order
from membership.scheduling import available_slots, group_by_day, hold_expiry, is_available

logger = logging.getLogger(__name__)

_ZONES = None


def _safe_zone(name, fallback="UTC"):
    """Validate a timezone name from the browser before storing or using it.

    The value arrives from client JS, so it is untrusted input that ends up in a
    stored field and in ZoneInfo(); checking it against the system's zone list
    keeps both honest.
    """
    global _ZONES
    if _ZONES is None:
        _ZONES = available_timezones()
    name = (name or "").strip()
    return name if name in _ZONES else fallback


def _price():
    try:
        return Decimal(str(settings.CONSULT_PRICE))
    except (InvalidOperation, TypeError):
        return Decimal("25.00")


def _abs(request, name, **kwargs):
    return request.build_absolute_uri(reverse(name, kwargs=kwargs) if kwargs
                                      else reverse(name))


def slot_context(request, display_zone):
    """Everything the picker needs, grouped in the viewer's own timezone."""
    slots = available_slots()
    return {
        "slot_days": group_by_day(slots, display_zone),
        "slot_count": len(slots),
        "display_zone": display_zone,
        "duration_minutes": settings.CONSULT_DURATION_MINUTES,
        "agent_timezone": settings.CONSULT_TIMEZONE,
        "lead_hours": settings.CONSULT_LEAD_HOURS,
    }


@require_POST
def book_consultation(request):
    """Hold the slot, then hand the visitor to PayPal.

    Returns JSON so the picker can show an error in place rather than losing the
    form to a full page reload.
    """
    raw_start = (request.POST.get("starts_at") or "").strip()
    name = (request.POST.get("name") or "").strip()[:120]
    email = (request.POST.get("email") or "").strip()[:254]
    notes = (request.POST.get("notes") or "").strip()[:2000]
    visitor_zone = _safe_zone(request.POST.get("timezone"))

    if not (name and email):
        return JsonResponse({"error": "Please give a name and an email."}, status=400)

    starts_at = parse_datetime(raw_start)
    if starts_at is None:
        return JsonResponse({"error": "That slot is not a valid time."}, status=400)
    if timezone.is_naive(starts_at):
        starts_at = starts_at.replace(tzinfo=ZoneInfo("UTC"))
    starts_at = starts_at.astimezone(ZoneInfo("UTC"))

    # Guard, not guarantee: the page may have been open for an hour. The
    # guarantee is the unique constraint caught below.
    if not is_available(starts_at):
        return JsonResponse(
            {"error": "Sorry — that time has just been taken. Please pick another."},
            status=409,
        )

    listing = None
    raw_pk = (request.POST.get("listing") or "").strip()
    if raw_pk.isdigit():
        listing = Property.objects.filter(pk=raw_pk, show_in_front=True).first()

    price, currency = _price(), settings.CONSULT_CURRENCY
    try:
        with transaction.atomic():
            booking = Consultation.objects.create(
                starts_at=starts_at,
                duration_minutes=settings.CONSULT_DURATION_MINUTES,
                name=name,
                email=email,
                visitor_timezone=visitor_zone,
                notes=notes,
                listing=listing,
                status=Consultation.STATUS_HOLD,
                hold_expires_at=hold_expiry(),
                amount=price,
                currency=currency,
            )
    except IntegrityError:
        # Someone else got the same slot between the check above and this insert.
        # This is the case the unique constraint exists for.
        logger.info("Slot %s was taken concurrently; asking for another.", starts_at)
        return JsonResponse(
            {"error": "Sorry — that time has just been taken. Please pick another."},
            status=409,
        )

    description = "30-minute akiya consultation"
    if listing:
        description = f"Akiya consultation — {listing.get_location_for_front()}"

    try:
        order_id, approve_url = create_order(
            amount=price,
            currency=currency,
            description=description,
            return_url=_abs(request, "consultation_return"),
            cancel_url=_abs(request, "consultation_cancelled"),
            reference=booking.pk,
            idempotency_key=f"consult-{booking.pk}",
        )
    except PayPalError as e:
        # Release the slot immediately rather than leaving it held for 20
        # minutes over a failure that had nothing to do with the visitor.
        booking.status = Consultation.STATUS_CANCELLED
        booking.save(update_fields=["status"])
        logger.error("Could not start checkout for booking %s: %s", booking.pk, e)
        return JsonResponse(
            {"error": "Payment could not be started. Please try again, or email us."},
            status=502,
        )

    booking.paypal_order_id = order_id
    booking.save(update_fields=["paypal_order_id"])
    return JsonResponse({"redirect": approve_url})


def consultation_return(request):
    """PayPal sends the payer back here with ?token=<order id>. Capture it."""
    order_id = (request.GET.get("token") or "").strip()
    booking = Consultation.objects.filter(paypal_order_id=order_id).first() if order_id else None

    if booking is None:
        logger.warning("Consultation return with unknown order token %r", order_id)
        return render(request, "consultation_result.html",
                      {"state": "unknown"}, status=404)

    # Reloading the confirmation page must not try to capture again.
    if booking.status in (Consultation.STATUS_PAID, Consultation.STATUS_COMPLETED):
        return render(request, "consultation_result.html",
                      {"state": "booked", "booking": booking})

    try:
        payment = capture_order(order_id)
    except PayPalError as e:
        logger.error("Capture failed for booking %s: %s", booking.pk, e)
        return render(request, "consultation_result.html",
                      {"state": "failed", "booking": booking}, status=502)

    if not payment["paid"]:
        # Approved but not captured, or a capture PayPal is holding for review.
        # Not money yet, so not a booking yet.
        logger.warning("Booking %s not paid: capture status %s",
                       booking.pk, payment.get("capture_status"))
        return render(request, "consultation_result.html",
                      {"state": "pending", "booking": booking})

    with transaction.atomic():
        booking.status = Consultation.STATUS_PAID
        booking.paid_at = timezone.now()
        booking.hold_expires_at = None
        booking.paypal_capture_id = payment["capture_id"]
        if payment["amount"]:
            try:
                booking.amount = Decimal(payment["amount"])
            except InvalidOperation:
                pass
        if payment["currency"]:
            booking.currency = payment["currency"]
        booking.save()

    # Email failures must not lose a paid booking; the row is already saved.
    try:
        from membership.consultation_mail import send_confirmation
        send_confirmation(booking)
    except Exception as e:
        logger.error("Booking %s is paid but its emails failed: %s", booking.pk, e)

    return render(request, "consultation_result.html",
                  {"state": "booked", "booking": booking})


def consultation_cancelled(request):
    """PayPal's cancel_url. Free the slot straight away."""
    order_id = (request.GET.get("token") or "").strip()
    if order_id:
        updated = Consultation.objects.filter(
            paypal_order_id=order_id, status=Consultation.STATUS_HOLD
        ).update(status=Consultation.STATUS_CANCELLED, hold_expires_at=None)
        if updated:
            logger.info("Released held slot for cancelled order %s", order_id)
    return redirect(f"{reverse('consultation')}?cancelled=1")
