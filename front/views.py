import json
import random
from django.db import models
from django.db.models import Q, F
from django.conf import settings
from django.shortcuts import render, redirect
from django.views import View
from inventory.models import Property, PropertyImage
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
PRICE_BUCKETS = [
    {"key": "u50", "label": "Under $50k", "gt": 0, "lte": 714},
    {"key": "50-100", "label": "$50k – $100k", "gt": 714, "lte": 1428},
    {"key": "100-200", "label": "$100k – $200k", "gt": 1428, "lte": 2857},
    {"key": "200-350", "label": "$200k – $350k", "gt": 2857, "lte": 5000},
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
    base_queryset = (
        Property.objects.prefetch_related("images")
        .annotate(
            has_any_image=models.Exists(
                PropertyImage.objects.filter(property=models.OuterRef("pk"))
            )
        )
        .filter(show_in_front=True, price__lte=5000, price__gt=0)
    )

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
        property_url=property_url,
        source=source,
    )

    # Confirmation email to the requester.
    html_message = render_to_string(
        "emails/interest_request.html",
        {"name": name, "property_url": property_url},
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
            f"<p><b>Message:</b> {message or '(none)'}</p>"
            f"<p><b>Property:</b> {property_url or '(from home page)'}</p>"
            f"<p>Review and mark as contacted in "
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
    user_email = (
        request.user.email
        if request.user.is_authenticated
        else request.COOKIES.get("email")
    )
    return render(
        request,
        "contact_seller.html",
        context={
            "property": property,
            "property_title": "Akiya in" if pk % 2 == 0 else "Japanese House in",
            "user_email": user_email,
            "user_just_registered": user_just_registered,
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
    properties = (
        Property.objects.filter(show_in_front=True, price__lte=5000, price__gt=0)
        .annotate(
            has_any_image=models.Exists(
                PropertyImage.objects.filter(property=models.OuterRef("pk"))
            )
        )
        .order_by("-featured", "price")[: settings.PROPERTIES_TO_DISPLAY]
    )
    return render(request, "home.html", context={"properties": properties})
