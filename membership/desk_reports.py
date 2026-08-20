"""Selling the pre-purchase desk report.

Four pages: the offer, a real worked example, PayPal's return, PayPal's cancel.

The example is the whole marketing argument, so it is not a screenshot or a
mock-up — it is the generator's actual output on a real listing, rendered live.
Anything else would drift from the product, and a buyer comparing the two would
be right to distrust the difference.

Payment is taken up front, which the inspection flow deliberately does not do.
The difference is access: an inspection needs someone inside a house the seller's
agent controls, while every input to this report is a published listing plus our
own comparables. Nothing about delivering it can be blocked by a third party, so
charging for it in advance is honest.

The price always comes from settings, never from the request — a price that
arrives from the browser is a price the browser can edit.
"""

import logging
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_POST

from inventory.desk_report import build_report
from inventory.models import Property
from inventory.utils import YEN_TO_USD
from membership.models import DeskReportOrder
from membership.paypal_orders import PayPalError, capture_order, create_order

logger = logging.getLogger(__name__)


def _price():
    try:
        return Decimal(str(settings.DESK_REPORT_PRICE))
    except (InvalidOperation, TypeError):
        return Decimal("39.00")


def _abs(request, name):
    return request.build_absolute_uri(reverse(name))


# Used only to pick the sample listing when one has not been pinned in settings.
#
# Completeness alone is the wrong measure, and picking that way produced a
# newly-built house with five bland findings — the least persuasive example on
# the site. What sells the report is a listing where it finds something: a
# rebuild restriction, an old building, an undisclosed utilities line. So a
# listing is scored on how much it gives the report to say, and the fields below
# are only the tiebreak.
_FIELDS_FOR_COMPLETENESS = (
    "city_planning", "land_rights", "land_category", "road_condition",
    "construction_date", "equipment", "renovation", "handover",
    "building_coverage_ratio", "zoning",
)

# (attribute, pattern, weight). Weight is roughly "how much a buyer would want
# to have known this before offering".
_DEMONSTRATIVE = (
    ("city_planning", r"市街化調整区域|urbanization control|urbanisation control", 6),
    ("land_category", r"農地|farmland|rice ?field", 6),
    ("land_rights", r"借地|leasehold|lease ?right", 5),
    ("road_condition", r"私道|private road", 3),
    ("setback", r"\d", 2),
)


# Some scraped titles are the transit string by mistake — "JR Gotemba Line
# Ashigara Station 4.1km". Fine in a listing grid, wrong at the top of a document
# that is meant to look like professional work, so such a listing is skipped when
# choosing the example.
_MANGLED_TITLE = re.compile(r"\d+\s*km|\bStation\b|\bminutes?\b", re.I)

# How long the chosen example is remembered. The choice walks a few hundred rows,
# and the offer page is not cached (it prefills the signed-in user's email), so
# without this every visit would redo the scan.
_SAMPLE_CACHE_KEY = "desk_report_sample_pk"
_SAMPLE_CACHE_SECONDS = 60 * 15


def _sample_candidates():
    """Listings worth building an example from, shortlisted in the database.

    Filtering first, rather than scoring an arbitrary slice of the table, is what
    makes the choice both cheap and correct: an unordered `[:400]` was excluding
    better candidates than the ones it kept.
    """
    from django.db.models import Q

    live = Property.objects.filter(
        show_in_front=True, price__gt=0
    ).exclude(location="").exclude(title="")

    shortlist = live.filter(
        Q(city_planning__icontains="control")
        | Q(city_planning__icontains="市街化調整")
        | Q(land_category__icontains="farmland")
        | Q(land_category__icontains="農地")
        | Q(land_rights__icontains="lease")
        | Q(road_condition__icontains="private road")
    )
    # Fall back to the general population only if nothing interesting exists,
    # so the page still works on a fresh or thin database.
    return list(shortlist[:300]) or list(live[:200])


def _sample_property():
    """The listing the public example is built from.

    Pinned by DESK_REPORT_SAMPLE_PK when set. Otherwise the listing that gives
    the report the most to say wins — see _DEMONSTRATIVE. Choosing rather than
    hard-coding means the example survives its subject being delisted, which will
    happen, and a dead example page is worse than an imperfect one.
    """
    pinned = settings.DESK_REPORT_SAMPLE_PK
    if pinned:
        listing = Property.objects.filter(pk=pinned).first()
        if listing:
            return listing
        logger.warning("DESK_REPORT_SAMPLE_PK=%s does not exist; picking one.",
                       pinned)

    from django.core.cache import cache

    cached_pk = cache.get(_SAMPLE_CACHE_KEY)
    if cached_pk:
        listing = Property.objects.filter(pk=cached_pk, show_in_front=True).first()
        if listing:
            return listing

    candidates = _sample_candidates()

    def score(listing):
        demonstrative = sum(
            weight for field, pattern, weight in _DEMONSTRATIVE
            if re.search(pattern, getattr(listing, field, "") or "", re.I)
        )
        # An old building gives the report its earthquake-standard finding.
        year = re.search(r"(1[89]\d{2}|20\d{2})", listing.construction_date or "")
        if year and int(year.group(1)) < 1981:
            demonstrative += 4
        # A listing that withholds its utilities line demonstrates the point the
        # whole report is built on: what has not been disclosed.
        if not (listing.equipment or "").strip(" -—"):
            demonstrative += 2

        filled = sum(
            1 for field in _FIELDS_FOR_COMPLETENESS
            if (getattr(listing, field, "") or "").strip() not in ("", "-", "—")
        )
        # pk as the final tiebreak keeps the choice stable between requests.
        return (demonstrative, filled, -listing.pk)

    candidates = [c for c in candidates if not _MANGLED_TITLE.search(c.title or "")]
    chosen = max(candidates, key=score, default=None)
    if chosen:
        cache.set(_SAMPLE_CACHE_KEY, chosen.pk, _SAMPLE_CACHE_SECONDS)
    return chosen


def _offer_context():
    return {
        "price_label": settings.DESK_REPORT_PRICE_LABEL,
        "turnaround_days": settings.DESK_REPORT_TURNAROUND_DAYS,
        "inventory_size": Property.objects.filter(show_in_front=True).count(),
    }


def desk_report_offer(request):
    """The offer page: what it is, what it costs, and the example."""
    listing = None
    raw_pk = (request.GET.get("listing") or "").strip()
    if raw_pk.isdigit():
        listing = Property.objects.filter(pk=raw_pk, show_in_front=True).first()

    sample = _sample_property()
    return render(request, "desk_report_offer.html", dict(
        _offer_context(),
        nav="desk_report",
        listing=listing,
        sample=sample,
        sample_report=build_report(sample) if sample else None,
    ))


@cache_page(60 * 15)
def desk_report_example(request):
    """The example, rendered by the generator that produces the real thing.

    Marked as an example on its face, and it names the listing it is about — the
    honesty is the point: a buyer can open that listing themselves and see what
    we added to it.
    """
    listing = _sample_property()
    if listing is None:
        return render(request, "desk_report_offer.html",
                      dict(_offer_context(), nav="desk_report", sample=None))

    report = build_report(listing)
    return render(request, "desk_report.html", dict(
        report,
        report_date=timezone.now().date(),
        # An example is not a draft: the human sections are described on the
        # offer page instead, so the example shows the finished shape.
        draft=False,
        sample=True,
        verdict="",
        inventory_size=Property.objects.filter(show_in_front=True).count(),
        yen_to_usd=YEN_TO_USD,
        price_label=settings.DESK_REPORT_PRICE_LABEL,
    ))


@require_POST
def order_desk_report(request):
    """Record the order, then hand the buyer to PayPal.

    JSON so the form can show an error in place rather than losing what was
    typed to a page reload.
    """
    email = (request.POST.get("email") or "").strip()[:254]
    if not email and request.user.is_authenticated:
        email = request.user.email
    name = (request.POST.get("name") or "").strip()[:120]
    notes = (request.POST.get("notes") or "").strip()[:2000]

    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    try:
        validate_email(email)
    except ValidationError:
        return JsonResponse(
            {"error": "Please give an email address we can send the report to."},
            status=400,
        )

    listing = None
    raw_pk = (request.POST.get("listing") or "").strip()
    if raw_pk.isdigit():
        listing = Property.objects.filter(pk=raw_pk).first()
    listing_url = (request.POST.get("listing_url") or "").strip()[:500]

    if not (listing or listing_url):
        return JsonResponse(
            {"error": "Which property is this about? Paste its link, or order "
                      "from the property's own page."},
            status=400,
        )

    price, currency = _price(), settings.DESK_REPORT_CURRENCY
    with transaction.atomic():
        order = DeskReportOrder.objects.create(
            email=email,
            name=name,
            user=request.user if request.user.is_authenticated else None,
            listing=listing,
            listing_url=(
                f"https://akiyainjapan.com{listing.get_public_url}"
                if listing else listing_url
            ),
            listing_location=(listing.get_location_for_front() if listing else ""),
            buyer_notes=notes,
            amount=price,
            currency=currency,
        )

    where = order.listing_location or "a listing"
    try:
        order_id, approve_url = create_order(
            amount=price,
            currency=currency,
            description=f"Pre-purchase desk report — {where}",
            return_url=_abs(request, "desk_report_paid"),
            cancel_url=_abs(request, "desk_report_cancelled"),
            reference=order.pk,
            idempotency_key=f"deskreport-{order.pk}",
        )
    except PayPalError as e:
        order.status = DeskReportOrder.STATUS_CANCELLED
        order.internal_notes = f"PayPal would not start checkout: {e}"
        order.save(update_fields=["status", "internal_notes"])
        logger.error("Could not start desk report checkout for %s: %s", order.pk, e)
        return JsonResponse(
            {"error": "Payment could not be started. Please try again, or email "
                      "hello@akiyainjapan.com."},
            status=502,
        )

    order.paypal_order_id = order_id
    order.save(update_fields=["paypal_order_id"])
    return JsonResponse({"redirect": approve_url})


def desk_report_paid(request):
    """PayPal's return_url: ?token=<order id>. Capture, then confirm."""
    order_id = (request.GET.get("token") or "").strip()
    order = (DeskReportOrder.objects.filter(paypal_order_id=order_id).first()
             if order_id else None)

    if order is None:
        logger.warning("Desk report return with unknown order token %r", order_id)
        return render(request, "desk_report_result.html",
                      dict(_offer_context(), state="unknown"), status=404)

    # Reloading the confirmation must not capture twice.
    if order.status in (DeskReportOrder.STATUS_PAID,
                        DeskReportOrder.STATUS_DELIVERED):
        return render(request, "desk_report_result.html",
                      dict(_offer_context(), state="paid", order=order))

    try:
        payment = capture_order(order_id)
    except PayPalError as e:
        logger.error("Desk report capture failed for %s: %s", order.pk, e)
        return render(request, "desk_report_result.html",
                      dict(_offer_context(), state="failed", order=order),
                      status=502)

    if not payment["paid"]:
        logger.warning("Desk report %s not paid: capture status %s",
                       order.pk, payment.get("capture_status"))
        return render(request, "desk_report_result.html",
                      dict(_offer_context(), state="pending", order=order))

    with transaction.atomic():
        order.status = DeskReportOrder.STATUS_PAID
        order.paid_at = timezone.now()
        order.paypal_capture_id = payment["capture_id"]
        if payment["amount"]:
            try:
                order.amount = Decimal(payment["amount"])
            except InvalidOperation:
                pass
        if payment["currency"]:
            order.currency = payment["currency"]
        order.save()

    # The money is in and the row is saved; an email failure must not look like
    # a failed purchase.
    try:
        _notify(order)
    except Exception as e:
        logger.error("Desk report %s is paid but its emails failed: %s",
                     order.pk, e)

    return render(request, "desk_report_result.html",
                  dict(_offer_context(), state="paid", order=order))


def desk_report_cancelled(request):
    """PayPal's cancel_url. Mark it abandoned so the funnel can see it."""
    order_id = (request.GET.get("token") or "").strip()
    if order_id:
        DeskReportOrder.objects.filter(
            paypal_order_id=order_id, status=DeskReportOrder.STATUS_PENDING
        ).update(status=DeskReportOrder.STATUS_CANCELLED)
    return render(request, "desk_report_result.html",
                  dict(_offer_context(), state="cancelled"))


def _notify(order):
    """Tell the buyer it is coming, and tell us it is owed."""
    from membership.utils import notification_email

    days = settings.DESK_REPORT_TURNAROUND_DAYS
    where = order.listing_location or order.listing_url or "the listing"

    notification_email(
        subject="Your desk report is being prepared",
        body=(
            f"Thanks — payment received for a pre-purchase desk report on "
            f"{where}.\n\n"
            f"We'll send it within {days} working days. Part of it is a call to "
            "the local municipal office about what may and may not be built on "
            "the parcel, which is why it takes a few days rather than minutes.\n\n"
            "If there is anything specific you want us to look at, reply to this "
            "email and we'll fold it in.\n\n"
            "— My Akiya in Japan\nhttps://akiyainjapan.com"
        ),
        to=[order.email],
    )

    notification_email(
        subject=f"PAID desk report owed — {where}",
        body=(
            f"Order #{order.pk}\n"
            f"Buyer: {order.name or '(no name)'} <{order.email}>\n"
            f"Listing: {order.listing_url or '(none given)'}\n"
            f"Paid: {order.amount} {order.currency}\n"
            f"Their notes: {order.buyer_notes or '(none)'}\n\n"
            f"Start with: manage.py desk_report {order.listing_id or '<pk>'}\n"
            "Then the municipal enquiry, the Japanese remarks, and the verdict."
        ),
        to=[settings.DESK_REPORT_NOTIFY_EMAIL],
    )
