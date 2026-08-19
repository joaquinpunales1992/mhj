import json
import random
import urllib.parse
from django.db import models
from django.db.models import Q, F
from django.conf import settings
from django.shortcuts import render, redirect
from django.views import View
from inventory.models import GeocodedPlace, Property, PropertyImage
from membership.metering import check_access
from membership.consultations import _safe_zone, slot_context
from inventory.images import thumb_url
from inventory.utils import (
    CURRENCY_PREFIX,
    city_key,
    convert_price_string,
    convert_yen_to_usd,
    infer_location,
    scatter_offset,
)
from django.core.mail import EmailMessage
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import cache_page
from django.template.loader import render_to_string
from django.core.paginator import Paginator


# Prefecture -> the lifestyle categories it qualifies for. Used both to resolve
# the /filter/<category>/ pages and to populate the city dropdown.
CITY_CATEGORIES = {
    "Tokyo": ["beach", "onsen"],
    "Osaka": ["onsen"],
    "Shizuoka": ["beach", "mountain", "onsen"],
    "Kanagawa": ["beach", "onsen"],
    "Aichi": ["onsen"],
    "Hyogo": ["mountain", "onsen"],
    "Chiba": ["beach"],
    "Saitama": ["mountain"],
    "Fukuoka": ["beach"],
    "Hiroshima": ["beach"],
    "Kyoto": ["mountain", "onsen"],
    "Nagoya": ["onsen"],
    "Kagawa": ["beach"],
    "Okayama": ["beach"],
    "Miyagi": ["snow", "onsen"],
    "Niigata": ["snow"],
    "Ishikawa": ["onsen"],
    "Nagano": ["snow", "mountain", "onsen"],
    "Gunma": ["mountain", "onsen"],
    "Tochigi": ["mountain", "onsen"],
    "Ibaraki": ["beach"],
    "Yamagata": ["snow", "onsen"],
    "Fukushima": ["snow", "onsen"],
    "Shimane": ["onsen"],
    "Tottori": ["mountain", "beach"],
    "Nagasaki": ["beach"],
    "Kumamoto": ["onsen", "mountain"],
    "Ehime": ["beach"],
    "Kagoshima": ["onsen", "mountain", "beach"],
    "Okinawa": ["beach"],
    "Aomori": ["snow"],
    "Akita": ["snow", "onsen"],
    "Yamaguchi": ["beach"],
    "Toyama": ["snow", "mountain"],
    "Gifu": ["mountain", "onsen"],
    "Wakayama": ["beach", "onsen", "mountain"],
    "Nara": ["mountain"],
    "Miyazaki": ["beach", "mountain"],
    "Tokushima": ["mountain"],
    "Oita": ["onsen"],
    "Fukui": ["snow"],
    "Shiga": ["mountain"],
    "Hokkaido": ["snow", "beach", "onsen"],
    "Kochi": ["beach"],
    "Saga": ["onsen"],
    "Mie": ["beach", "onsen"],
}

# Cities offered in the dropdown, alphabetical.
PREFECTURES = sorted(CITY_CATEGORIES.keys())

# Price ranges. Bounds are in the model's stored unit (man-yen, ~70 USD each);
# labels are the rounded USD equivalents shown to users. price__gt/__lte.
# Labels are built from CURRENCY_PREFIX rather than written out, so the filter
# cannot drift back to a bare "$" while every price beside it says "US$" — which
# is exactly what happened: these labels are the live ones, and a stale hardcoded
# set in home.html hid them from a search-and-replace over the templates.
PRICE_BUCKETS = [
    {"key": "u50", "label": f"Under {CURRENCY_PREFIX}50k", "gt": 0, "lte": 714},
    {"key": "50-100", "label": f"{CURRENCY_PREFIX}50k – {CURRENCY_PREFIX}100k", "gt": 714, "lte": 1428},
    {"key": "100-200", "label": f"{CURRENCY_PREFIX}100k – {CURRENCY_PREFIX}200k", "gt": 1428, "lte": 2857},
    {"key": "200-350", "label": f"{CURRENCY_PREFIX}200k – {CURRENCY_PREFIX}350k", "gt": 2857, "lte": 5000},
]
PRICE_BUCKETS_BY_KEY = {b["key"]: b for b in PRICE_BUCKETS}


def _apply_browse_filters(queryset, request):
    """Narrow a property queryset by the ?city and ?price query params.

    Returns (queryset, selected_city, selected_price) so the view can echo the
    current selection back to the template. Unknown values are ignored.
    """
    selected_city = (request.GET.get("city") or "").strip()
    selected_price = (request.GET.get("price") or "").strip()

    if selected_city in CITY_CATEGORIES:
        queryset = queryset.filter(location__icontains=selected_city)

    bucket = PRICE_BUCKETS_BY_KEY.get(selected_price)
    if bucket:
        queryset = queryset.filter(price__gt=bucket["gt"], price__lte=bucket["lte"])

    return queryset, selected_city, selected_price


def _available_cities():
    """Prefectures that actually have browsable listings, with counts.

    Returns [{"name", "count"}] ordered by count desc. Avoids offering dropdown
    options that would land the user on an empty page. One query + a cheap
    in-Python scan; the home page is cached hourly so this is not hot.
    """
    locations = Property.objects.filter(
        show_in_front=True, price__gt=0, price__lte=5000
    ).values_list("location", flat=True)

    counts = {}
    for loc in locations:
        low = (loc or "").lower()
        for pref in CITY_CATEGORIES:
            if pref.lower() in low:
                counts[pref] = counts.get(pref, 0) + 1

    return [
        {"name": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _browse_filter_context(selected_city="", selected_price=""):
    """Shared template context for the city/price filter controls."""
    return {
        "cities": _available_cities(),
        "selected_city": selected_city,
        "price_buckets": PRICE_BUCKETS,
        "selected_price": selected_price,
    }


@cache_page(60 * 60)
def display_home(request):
    # No prefetch_related("images") here: the card template reads
    # property.get_ordered_images, which calls .order_by() on the related
    # manager and therefore ignores the prefetch cache and re-queries anyway.
    # Prefetching just loaded ~36k PropertyImage rows per render and threw
    # them away — the single biggest cost of a cache miss.
    base_queryset = Property.objects.annotate(
        has_any_image=models.Exists(
            PropertyImage.objects.filter(property=models.OuterRef("pk"))
        )
    ).filter(show_in_front=True, price__lte=5000, price__gt=0, has_any_image=True)

    base_queryset, selected_city, selected_price = _apply_browse_filters(
        base_queryset, request
    )

    featured = list(base_queryset.filter(featured=True))
    non_featured = list(base_queryset.filter(featured=False))
    random.shuffle(featured)
    random.shuffle(non_featured)
    properties = featured + non_featured

    paginator = Paginator(properties, settings.PROPERTIES_PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "home.html",
        context={
            "properties": page.object_list,
            "page": page,
            "nav": "home",
            **_browse_filter_context(selected_city, selected_price),
        },
    )


# How many property cards the map's list panel may request at once. Caps both
# the query and the number of image URLs travelling over the wire.
MAP_CARD_BATCH_LIMIT = 60


# Cards rendered into the panel server-side on first load. The map is a JS
# application, so without these the site's most important page would serve
# Google an empty shell — these keep real listings in the HTML. JS replaces
# them as soon as the viewport is known.
MAP_INITIAL_CARDS = 24


def map_view(request):
    """Split browse view: map on the left, listings for the current viewport
    on the right.

    NOT cache_page'd: the response embeds the signed-in user's saved property
    ids, and a shared per-URL cache would hand one account's favourites to
    everyone. The expensive part (all map points) is cached at
    map_properties_json instead, which is identical for every visitor.

    Deliberately NOT the site's landing page: "/" is indexed and ranks, and a
    JS-driven map would serve crawlers an empty shell. The map is promoted
    heavily from "/" instead, so it is the main experience without disturbing
    the indexed URL.

    The panel's initial contents and the region links are still rendered
    server-side — see MAP_INITIAL_CARDS — so this page has real content of its
    own. JS takes over as soon as the viewport is known.
    """
    base = (
        Property.objects.annotate(
            has_any_image=models.Exists(
                PropertyImage.objects.filter(property=models.OuterRef("pk"))
            )
        )
        .filter(show_in_front=True, price__gt=0, has_any_image=True)
        .exclude(location="")
    )
    # Same ?city / ?price params the listing page uses, so a search started on
    # "/" carries straight through to the map instead of being dropped.
    base, selected_city, selected_price = _apply_browse_filters(base, request)
    initial = base.order_by("-featured", "-created_at")[:MAP_INITIAL_CARDS]

    # Ids this user has favourited, so hearts render filled on first paint
    # rather than flickering on after a second request.
    saved_ids = (
        list(request.user.saved_properties.values_list("property_id", flat=True))
        if request.user.is_authenticated
        else []
    )

    bucket = PRICE_BUCKETS_BY_KEY.get(selected_price)
    return render(
        request,
        "map.html",
        context={
            "nav": "map",
            "saved_ids_json": json.dumps(saved_ids),
            "card_batch_limit": MAP_CARD_BATCH_LIMIT,
            "initial_properties": initial,
            "cities": _available_cities(),
            "selected_city": selected_city,
            "selected_price": selected_price,
            "price_buckets": PRICE_BUCKETS,
            # Bucket bounds are in 万; the map filters on the USD strings it
            # already has, so hand the client plain USD limits.
            "price_min_usd": int(bucket["gt"] * 10000 * 0.007) if bucket else "",
            "price_max_usd": int(bucket["lte"] * 10000 * 0.007) if bucket else "",
        },
    )


@cache_page(60 * 30)
def map_properties_json(request):
    """Every mappable property as one compact JSON payload.

    Served whole rather than per-viewport: ~10k points is a few hundred KB
    before gzip, and shipping it once lets the client cluster and filter with
    no further round trips. A bbox endpoint would mean a query per pan on a
    low-RAM box, which is the worse trade here. Cached for 30 minutes since the
    scraper only refreshes periodically.

    Keys are deliberately one character — at 10k rows the field names are a
    large fraction of the payload.
    """
    places = {
        p.key: (p.latitude, p.longitude)
        for p in GeocodedPlace.objects.exclude(latitude__isnull=True)
    }

    rows = (
        Property.objects.filter(show_in_front=True, price__gt=0)
        .exclude(location="")
        .annotate(
            has_any_image=models.Exists(
                PropertyImage.objects.filter(property=models.OuterRef("pk"))
            )
        )
        .filter(has_any_image=True)
        .values_list("pk", "title", "price", "location", "floor_plan")
    )

    points = []
    for pk, title, price, location, floor_plan in rows:
        coords = places.get(city_key(location))
        if not coords:
            continue
        dlat, dlng = scatter_offset(pk)
        points.append(
            {
                "i": pk,
                "a": round(coords[0] + dlat, 5),
                "o": round(coords[1] + dlng, 5),
                "p": convert_yen_to_usd(convert_price_string(price)),
                "t": (title or "")[:70],
                "l": infer_location(location),
                "f": (floor_plan or "")[:20],
            }
        )

    return JsonResponse(
        {"count": len(points), "points": points},
        json_dumps_params={"separators": (",", ":")},
    )


@cache_page(60 * 30)
def map_property_cards_json(request):
    """Card details for a specific batch of property ids.

    Split from map_properties_json because image URLs are ~137 characters each
    — carrying them for every property would add well over a megabyte to a
    payload that exists to place markers. The list panel only ever shows a
    screenful, so it asks for just those ids.
    """
    raw = (request.GET.get("ids") or "").split(",")
    ids = []
    for value in raw[:MAP_CARD_BATCH_LIMIT]:
        value = value.strip()
        if value.isdigit():
            ids.append(int(value))

    if not ids:
        return JsonResponse({"cards": []})

    def short(value, limit):
        """Card fields are one line each; scraped values are often a paragraph.

        floor_plan in particular can run to a full room-by-room description
        ("3SLDK (living and dining kitchen, 18 tatami mats...)"), which would
        blow the card layout apart.
        """
        text = (value or "").strip()
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

    properties = Property.objects.filter(pk__in=ids, show_in_front=True)
    cards = {}
    for prop in properties:
        image = prop.get_ordered_images().first()
        # str(), not .url: image.file holds a full external URL (homes.jp /
        # SUUMO), and FileField.url would prepend MEDIA_URL and percent-encode
        # it into a broken /media/https%3A/... path. The listing templates
        # render {{ image.file }} directly for the same reason.
        cards[prop.pk] = {
            "i": prop.pk,
            "t": prop.get_title_for_front(),
            "p": prop.get_price_for_front,
            "l": prop.get_location_for_front(),
            "f": short(prop.floor_plan, 28),
            "b": short(prop.building_area, 18),
            "u": prop.get_public_url,
            # Two URLs: the proxied thumbnail the card actually displays, and the
            # original as an onerror fallback so a proxy outage degrades to a
            # heavy card rather than a broken one. The popup reuses both.
            "img": thumb_url(str(image.file)) if image and image.file else "",
            "imgf": str(image.file) if image and image.file else "",
        }

    # Preserve the caller's ordering — the panel has already sorted the ids.
    return JsonResponse({"cards": [cards[i] for i in ids if i in cards]})


def about(request):
    return render(request, "about.html")


def how_to_buy(request):
    return render(request, "how_to_buy.html")


def faqs(request):
    return render(request, "faqs.html")


def pricing(request):
    """One page showing every tier in order.

    Replaces the situation where the site advertised three prices for two
    products — a $4.99 tier that could not be bought, a $10/mo subscription and
    a $25 call — with nothing explaining which a visitor needed.

    Pro and the consultation are deliberately independent: one is a research
    tool, the other is advice. Neither is a prerequisite for the other.
    """
    subscription = getattr(request.user, "subscription", None)
    return render(
        request,
        "pricing.html",
        {
            "nav": "pricing",
            "anon_limit": settings.VIEW_LIMIT_ANONYMOUS,
            "free_limit": settings.VIEW_LIMIT_FREE,
            "pro_price": settings.PRO_PRICE_LABEL,
            "consult_price": settings.CONSULT_PRICE_LABEL,
            "booking_url": settings.CONSULT_BOOKING_URL,
            "already_pro": bool(subscription and subscription.is_active),
        },
    )


def legacy_premium_redirect(request):
    """The old $4.99 page. Permanent redirect rather than a delete: it is in the
    sitemap, so anything it has accumulated in search should point at the real
    prices instead of 404ing."""
    return redirect("pricing", permanent=True)


def consultation(request):
    """Landing page and booking flow for the paid orientation call.

    Booking, payment and the calendar live here now (see
    membership/consultations.py). Owning the flow is what lets the booking arrive
    already attached to the property that prompted it, and lets the confirmation
    state the time in both the buyer's zone and the agent's.

    CONSULT_BOOKING_URL survives as a fallback for the one case the internal flow
    cannot cover: no PayPal credentials means no way to take the money, and an
    external scheduler beats a checkout that fails.
    """
    # A booking that came from a property page carries ?property=<pk>. Prefill
    # the scheduler's notes field with it so the call arrives already knowing
    # which house prompted it — otherwise that context is lost at the click and
    # has to be re-established on the call.
    prop = None
    raw_pk = (request.GET.get("property") or "").strip()
    if raw_pk.isdigit():
        prop = Property.objects.filter(pk=raw_pk, show_in_front=True).first()

    booking_url = settings.CONSULT_BOOKING_URL
    if booking_url and prop:
        note = (
            f"Interested in: {prop.get_title_for_front()} — "
            f"{prop.get_location_for_front()}, {prop.get_price_for_front} — "
            f"https://akiyainjapan.com{prop.get_public_url}"
        )
        separator = "&" if "?" in booking_url else "?"
        booking_url = f"{booking_url}{separator}notes={urllib.parse.quote(note)}"

    # Only fall back to the external scheduler when we genuinely cannot charge.
    can_charge = bool(settings.PAYPAL_CLIENT_ID and settings.PAYPAL_CLIENT_SECRET)

    context = {
        "booking_url": booking_url if not can_charge else "",
        "can_charge": can_charge,
        "consult_price": settings.CONSULT_PRICE_LABEL,
        "property": prop,
        "cancelled": request.GET.get("cancelled") == "1",
    }
    if can_charge:
        # The picker renders in the visitor's own timezone. The browser tells us
        # which one via ?tz= on first load; until then UTC, which is honest
        # rather than guessing from an IP.
        display_zone = _safe_zone(request.GET.get("tz"))
        context.update(slot_context(request, display_zone))

    return render(request, "consultation.html", context)


@cache_page(60 * 60)
def region_listing(request, region):
    """SEO landing page for one prefecture, e.g. /houses-in-tokyo/.

    Reuses the home grid/template but scoped to a region, with a unique title,
    description and H1 so it can rank (and become a Google sitelink) on its own.
    """
    region_name = next(
        (name for name in CITY_CATEGORIES if name.lower() == region.lower()), None
    )
    if not region_name:
        return redirect("home")

    # See display_home: get_ordered_images bypasses the prefetch cache, so
    # prefetch_related("images") is pure overhead here too.
    base_queryset = Property.objects.annotate(
        has_any_image=models.Exists(
            PropertyImage.objects.filter(property=models.OuterRef("pk"))
        )
    ).filter(
        show_in_front=True,
        price__lte=5000,
        price__gt=0,
        location__icontains=region_name,
        has_any_image=True,
    )

    featured = list(base_queryset.filter(featured=True))
    non_featured = list(base_queryset.filter(featured=False))
    random.shuffle(featured)
    random.shuffle(non_featured)
    properties = featured + non_featured

    paginator = Paginator(properties, settings.PROPERTIES_PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))
    count = len(properties)

    return render(
        request,
        "home.html",
        context={
            "properties": page.object_list,
            "page": page,
            "nav": "home",
            "region_name": region_name,
            "page_title": (
                f"Houses & Akiya for Sale in {region_name}, Japan | My Akiya in Japan"
            ),
            "page_description": (
                f"Browse {count} affordable homes and akiya for sale in {region_name}, "
                f"Japan — photos, prices and full details, with help through the entire "
                f"purchase process."
            ),
            "canonical_url": request.build_absolute_uri(request.path),
            **_browse_filter_context(region_name, ""),
        },
    )


@csrf_exempt
def submit_premium_request(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request"}, status=400)

    data = json.loads(request.body)
    user_email = (data.get("user_email") or "").strip()
    property_url = data.get("url")

    if not user_email:
        # The frontend renders the button regardless of whether we have a
        # captured email, so anonymous clicks send empty submissions. Reject
        # so we stop persisting rows we can't act on. The JS can surface a
        # "log in or register first" prompt.
        return JsonResponse({"error": "user_email required"}, status=400)

    # Premium requests now come straight from the upgrade page — anonymous
    # visitors type their email here instead of going through a separate
    # sign-up page first. Treat a brand-new email as a registration so the
    # admin "new user" notification still fires once.
    is_new_registration = request.COOKIES.get("email", "") != user_email

    # Persist so the requests are reviewable in /admin/ even if email fails.
    from membership.models import PremiumRequest
    from membership.utils import notification_email, notify_user_registered_via_email

    PremiumRequest.objects.create(
        user_email=user_email or "",
        property_url=property_url or "",
    )

    # Confirmation email to the requester.
    html_message = render_to_string(
        "emails/premium_request.html", {"property_url": property_url}
    )
    confirmation = EmailMessage(
        subject="Your Akiya in Japan - Premium Account Request",
        body=html_message,
        from_email="hello@akiyainjapan.com",
        to=[user_email],
        reply_to=["hello@akiyainjapan.com"],
    )
    confirmation.content_subtype = "html"
    try:
        confirmation.send()
    except Exception as e:
        print(f"Error sending confirmation email: {e}")

    # Clear admin-facing notification so you can act on it.
    notification_email(
        subject=f"PREMIUM REQUEST - {user_email}",
        body=(
            f"<p>New premium account request.</p>"
            f"<p><b>Email:</b> {user_email}</p>"
            f"<p><b>Property:</b> {property_url or '(not provided)'}</p>"
            f"<p>Review and mark as contacted in "
            f"<a href='https://akiyainjapan.com/admin/membership/premiumrequest/'>"
            f"the admin panel</a>.</p>"
        ),
    )

    if is_new_registration:
        notify_user_registered_via_email(user_email)

    # Remember the visitor so they stay recognised across the site (this is the
    # cookie the gating checks and the upgrade page read), mirroring the old
    # register-via-email step we just folded into this endpoint.
    response = JsonResponse({"message": "Email sent"})
    response.set_cookie("email", user_email)
    return response


@csrf_exempt
def submit_interest_request(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request"}, status=400)

    data = json.loads(request.body)
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    message = (data.get("message") or "").strip()
    property_url = (data.get("property_url") or "").strip()

    # Qualification fields from the CTA form. Regions arrives as a list (home
    # page multi-select); store it comma-joined.
    regions_raw = data.get("regions") or []
    if isinstance(regions_raw, str):
        regions_raw = [regions_raw]
    regions = ", ".join(r.strip() for r in regions_raw if r and r.strip())
    budget = (data.get("budget") or "").strip()
    timeline = (data.get("timeline") or "").strip()
    visited_japan = (data.get("visited_japan") or "").strip()

    if not name or not email:
        return JsonResponse({"error": "name and email are required"}, status=400)

    from membership.models import InterestRequest
    from membership.utils import notification_email

    source = (
        InterestRequest.SOURCE_PROPERTY
        if property_url
        else InterestRequest.SOURCE_HOME
    )

    # Persist so requests are reviewable in /admin/ even if email fails.
    InterestRequest.objects.create(
        name=name,
        email=email,
        message=message,
        regions=regions,
        budget=budget,
        timeline=timeline,
        visited_japan=visited_japan,
        property_url=property_url,
        source=source,
    )

    # Confirmation email to the requester. This is the highest-intent moment in
    # the whole funnel — they've just raised their hand — so it carries the
    # booking call-to-action rather than only promising that someone will call.
    html_message = render_to_string(
        "emails/interest_request.html",
        {
            "name": name,
            "property_url": property_url,
            "booking_url": settings.CONSULT_BOOKING_URL,
            "consult_price": settings.CONSULT_PRICE_LABEL,
        },
    )
    confirmation = EmailMessage(
        subject="Your Akiya in Japan - We received your enquiry",
        body=html_message,
        from_email="hello@akiyainjapan.com",
        to=[email],
        reply_to=["hello@akiyainjapan.com"],
    )
    confirmation.content_subtype = "html"
    try:
        confirmation.send()
    except Exception as e:
        print(f"Error sending confirmation email: {e}")

    # Admin-facing notification so you can act on it.
    notification_email(
        subject=f"EXPRESSION OF INTEREST - {name}",
        body=(
            f"<p>New expression of interest.</p>"
            f"<p><b>Name:</b> {name}</p>"
            f"<p><b>Email:</b> {email}</p>"
            f"<p><b>Region(s):</b> {regions or '(not provided)'}</p>"
            f"<p><b>Budget:</b> {budget or '(not provided)'}</p>"
            f"<p><b>Timeline:</b> {timeline or '(not provided)'}</p>"
            f"<p><b>Visited Japan:</b> {visited_japan or '(not provided)'}</p>"
            f"<p><b>Message:</b> {message or '(none)'}</p>"
            f"<p><b>Property:</b> {property_url or '(from home page)'}</p>"
            f"<p>They've been sent the booking link automatically. Set the "
            f"status as it moves — and set it to Dead with a reason the moment "
            f"it stops, in "
            f"<a href='https://akiyainjapan.com/admin/membership/interestrequest/'>"
            f"the admin panel</a>.</p>"
        ),
    )

    return JsonResponse({"message": "Request received"})


@csrf_exempt
def send_booking_confirmation(request):
    if request.method == "POST":
        data = json.loads(request.body)

        user_email = data.get("user_email")
        property_url = data.get("url")

        # Render the email template
        html_message = render_to_string(
            "emails/booking_confirmation.html", {"property_url": property_url}
        )

        email = EmailMessage(
            subject="Your Akiya in Japan - Booking Confirmation",
            body=html_message,
            from_email="hello@akiyainjapan.com",
            to=[user_email],
            bcc=["joaquinpunales@gmail.com"],
            reply_to=["hello@akiyainjapan.com"],
        )

        email.content_subtype = "html"
        try:
            email.send()
        except Exception as e:
            print(f"Error sending email: {e}")

        return JsonResponse({"message": "Email sent"})
    return JsonResponse({"error": "Invalid request"}, status=400)


def update_like_count(request, property_id, user_email=None):
    if request.method == "POST":
        data = json.loads(request.body)
        property_id = data.get("property_id")

        if not property_id:
            return JsonResponse({"error": "Invalid data"}, status=400)
        try:
            property = Property.objects.get(pk=property_id)
            property.likes += 1
            property.save()
            return JsonResponse({"likes": property.likes})
        except Property.DoesNotExist:
            return JsonResponse({"error": "Property not found"}, status=404)

    return JsonResponse({"error": "Invalid request method"}, status=405)


def property_detail(request, pk, user_just_registered=0):
    property = Property.objects.filter(pk=pk).first()
    if property is None:
        # Listing no longer exists (deleted / expired). Without this guard the
        # page renders broken with property=None; send the visitor home instead.
        return redirect("home")
    user_email = (
        request.user.email
        if request.user.is_authenticated
        else request.COOKIES.get("email")
    )
    # Metered access: records this view and decides whether the detail is
    # locked. Price, floor plan and location render regardless.
    access = check_access(request, property.pk)
    # How many photos the gate is holding back, for the tile that replaces them.
    # Counted here rather than in the template because the number is only
    # meaningful against the cap check_access has just decided, and the template
    # cannot subtract one variable from another. 0 when nothing is withheld, which
    # is also what keeps the tile from rendering on a listing with 3 photos or
    # fewer — a gate offering "0 more photos" reads as a bug.
    photo_cap = access.get("photo_limit")
    photos_withheld = (
        max(0, property.images.count() - photo_cap) if photo_cap else 0
    )
    # Favourite state for the heart on the gallery. Rendered server-side so the
    # heart is already filled on first paint rather than popping in after a
    # fetch — the map does the same thing with its saved_ids_json.
    is_saved = (
        request.user.is_authenticated
        and property.saved_by.filter(user=request.user).exists()
    )
    return render(
        request,
        "contact_seller.html",
        context={
            "property": property,
            "property_title": "Akiya in" if pk % 2 == 0 else "Japanese House in",
            "user_email": user_email,
            "user_just_registered": user_just_registered,
            "access": access,
            "photos_withheld": photos_withheld,
            "is_saved": is_saved,
            "saves_count": property.saved_by.count(),
            "pro_price": settings.PRO_PRICE_LABEL,
            "free_limit": settings.VIEW_LIMIT_FREE,
        },
    )


def legacy_contact_seller_redirect(request, pk):
    return redirect("property_detail", pk=pk, permanent=True)


def legacy_contact_seller_optional_redirect(request, pk, user_just_registered):
    return redirect(
        "property_detail",
        pk=pk,
        permanent=True,
        user_just_registered=user_just_registered,
    )


def filter_properties(request, category):
    city_filters = Q()
    for city in [
        city for city, categories in CITY_CATEGORIES.items() if category in categories
    ]:
        city_filters |= Q(location__icontains=city)

    properties = (
        Property.objects.filter(show_in_front=True, price__lte=5000, price__gt=0)
        .filter(city_filters)
        .annotate(
            has_any_image=models.Exists(
                PropertyImage.objects.filter(property=models.OuterRef("pk"))
            )
        )
        .filter(has_any_image=True)
        .order_by("-featured", "price")
    )

    properties, selected_city, selected_price = _apply_browse_filters(
        properties, request
    )

    paginator = Paginator(properties, settings.PROPERTIES_PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "home.html",
        context={
            "properties": page.object_list,
            "page": page,
            "nav": category,
            **_browse_filter_context(selected_city, selected_price),
        },
    )


def redirect_404_view(request, exception=None):
    # Any broken / no-longer-existing URL sends the visitor to the homepage.
    return redirect("home")
