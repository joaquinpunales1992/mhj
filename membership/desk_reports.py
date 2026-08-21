"""The desk report as a Pro benefit.

Three pages: the offer, a real worked example, and the claim endpoint the
property page posts to.

It used to be a US$39 product. Charging for it was optimising the wrong number:
somebody asking us to research a specific house is the highest-intent signal on
the site, and the referral that may follow a purchase is worth orders of
magnitude more than a one-off fee. So it is included with Pro — one report a
month, three per subscription — and the property page shows what the report
actually found on the listing being viewed, with the reasoning withheld.

The example is the whole marketing argument, so it is not a screenshot or a
mock-up: it is the generator's real output on a real listing, rendered live.
Anything else would drift from the product, and a buyer comparing the two would
be right to distrust the difference.

Allowance rules and their reasoning live in membership.desk_report_allowance.
They are re-checked here at the moment of claiming, never trusted from the page —
the button's state was decided when the page was rendered.
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
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from inventory.desk_report import build_report, draft_verdict
from inventory.models import Property
from inventory.utils import YEN_TO_USD
from membership.desk_report_allowance import allowance_for, claim_error
from membership.models import DeskReportRequest

logger = logging.getLogger(__name__)


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
# How many of the best candidates the daily rotation draws from.
_SAMPLE_ROTATION = 5


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
    if not candidates:
        return None

    # Rotate daily through the strongest few rather than fixing on one house
    # forever: the example is more convincing when a returning visitor sees it on
    # a different property, and it spreads the risk of the one chosen listing
    # being delisted. Keyed on the date so the choice is stable within a day and
    # the page cache stays useful.
    ranked = sorted(candidates, key=score, reverse=True)[:_SAMPLE_ROTATION]
    chosen = ranked[timezone.now().toordinal() % len(ranked)]
    cache.set(_SAMPLE_CACHE_KEY, chosen.pk, _SAMPLE_CACHE_SECONDS)
    return chosen


def _offer_context():
    from membership.desk_report_allowance import _per_month, _window_days

    return {
        "report_total": _per_month(),
        "window_days": _window_days(),
        "pro_price": settings.PRO_PRICE_LABEL,
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
        allowance=allowance_for(request.user),
    ))


# Framed by the property page's expandable panel, so it has to permit framing
# from our own origin: Django's clickjacking middleware defaults to DENY, which
# refuses even same-origin frames and shows the visitor "refused to connect".
# Outside cache_page so the header is applied to cached responses too.
@xframe_options_sameorigin
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
        # Served as part of a light-themed site, not as a standalone document.
        force_light=True,
        # The example reads as a finished report, so it carries a verdict. It is
        # composed from the findings themselves — see draft_verdict — rather than
        # written for one listing, because the example rotates.
        verdict=draft_verdict(report),
        inventory_size=Property.objects.filter(show_in_front=True).count(),
        yen_to_usd=YEN_TO_USD,
        pro_price=settings.PRO_PRICE_LABEL,
    ))


@require_POST
def request_desk_report(request):
    """A Pro member claims a report on a listing.

    Returns JSON so the property page can swap the panel in place rather than
    reloading the listing and losing the reader's position.
    """
    if not request.user.is_authenticated:
        return JsonResponse(
            {"error": "Please sign in.", "login_url": reverse("account_login")},
            status=401,
        )

    listing = None
    raw_pk = (request.POST.get("listing") or "").strip()
    if raw_pk.isdigit():
        listing = Property.objects.filter(pk=raw_pk).first()
    listing_url = (request.POST.get("listing_url") or "").strip()[:500]

    if not (listing or listing_url):
        return JsonResponse(
            {"error": "Which property is this about?"}, status=400
        )

    # Re-checked here, not trusted from the page: the button's state was decided
    # when the page was rendered, and two tabs can both show it enabled.
    refusal = claim_error(request.user, listing)
    if refusal:
        return JsonResponse({"error": refusal}, status=409)

    email = (request.user.email or "").strip()
    if not email:
        return JsonResponse(
            {"error": "Your account has no email address for us to send it to. "
                      "Add one in your account settings."},
            status=400,
        )

    with transaction.atomic():
        claim = DeskReportRequest.objects.create(
            email=email,
            name=(request.user.get_full_name() or "")[:120],
            user=request.user,
            listing=listing,
            listing_url=(
                f"https://akiyainjapan.com{listing.get_public_url}"
                if listing else listing_url
            ),
            listing_location=(listing.get_location_for_front() if listing else ""),
            buyer_notes=(request.POST.get("notes") or "").strip()[:2000],
        )

    try:
        _notify(claim)
    except Exception as e:
        # The claim is saved; an email failure must not look like a refusal.
        logger.error("Desk report request %s saved but not emailed: %s",
                     claim.pk, e)

    remaining = allowance_for(request.user)
    return JsonResponse({
        "ok": True,
        "days": settings.DESK_REPORT_TURNAROUND_DAYS,
        "email": email,
        "remaining": remaining["remaining"],
    })


def _notify(claim):
    """Tell the member it is coming, and tell us it is owed."""
    from membership.utils import notification_email

    days = settings.DESK_REPORT_TURNAROUND_DAYS
    where = claim.listing_location or claim.listing_url or "the listing"

    notification_email(
        subject="Your desk report is being prepared",
        body=(
            f"Thanks — we're preparing your desk report on {where}.\n\n"
            f"It will reach you within {days} working days. Part of it is a call "
            "to the local municipal office about what may and may not be built on "
            "the parcel, which is why it takes a few days rather than minutes.\n\n"
            "If there is anything specific you want us to look at, reply to this "
            "email and we'll fold it in.\n\n"
            "— My Akiya in Japan\nhttps://akiyainjapan.com"
        ),
        to=[claim.email],
    )

    notification_email(
        subject=f"Pro desk report owed — {where}",
        body=(
            f"Request #{claim.pk}\n"
            f"Member: {claim.name or '(no name)'} <{claim.email}>\n"
            f"Listing: {claim.listing_url or '(none given)'}\n"
            # The property page no longer asks for notes, so this line only
            # appears when something actually arrived — from a reply to the
            # confirmation email, or a future surface that does ask.
            + (f"Their notes: {claim.buyer_notes}\n" if claim.buyer_notes else "")
            + "\n"
            f"Start with: manage.py desk_report {claim.listing_id or '<pk>'}\n"
            "Then the municipal enquiry, the Japanese remarks, and the verdict."
        ),
        to=[settings.DESK_REPORT_NOTIFY_EMAIL],
    )
