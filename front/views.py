import json
from django.db.models import Q, F
from django.conf import settings
from django.shortcuts import render, redirect
from django.views import View
from inventory.models import Property
from django.core.mail import EmailMessage
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.template.loader import render_to_string


def display_home(request):
    properties = Property.objects.filter(
        show_in_front=True, price__lte=1500, price__gt=0
    ).order_by("-featured", "price")[: settings.PROPERTIES_TO_DISPLAY]
    return render(
        request, "home.html", context={"properties": properties, "nav": "home"}
    )


@csrf_exempt
def submit_premium_request(request):
    if request.method == "POST":
        data = json.loads(request.body)

        user_email = data.get("user_email")
        property_url = data.get("url")

        # Render the email template
        html_message = render_to_string(
            "emails/premium_request.html", {"property_url": property_url}
        )

        email = EmailMessage(
            subject="Your Akiya in Japan - Premium Account Request",
            body=html_message,
            from_email="hello@myakiyainjapan.com",
            to=[user_email],
            bcc=["joaquinpunales@gmail.com"],
            reply_to=["hello@myakiyainjapan.com"],
        )

        email.content_subtype = "html"
        try:
            email.send()
        except Exception as e:
            print(f"Error sending email: {e}")

        return JsonResponse({"message": "Email sent"})
    return JsonResponse({"error": "Invalid request"}, status=400)


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
            from_email="hello@myakiyainjapan.com",
            to=[user_email],
            bcc=["joaquinpunales@gmail.com"],
            reply_to=["hello@myakiyainjapan.com"],
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

    city_categories = {
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

    city_filters = Q()
    for city in [
        city for city, categories in city_categories.items() if category in categories
    ]:
        city_filters |= Q(location__icontains=city)

    properties = (
        Property.objects.filter(show_in_front=True, price__lte=1500, price__gt=0)
        .filter(city_filters)
        .order_by("-featured", "price")[: settings.PROPERTIES_TO_DISPLAY]
    )
    return render(
        request, "home.html", context={"properties": properties, "nav": category}
    )


def redirect_404_view(request, exception=None):
    properties = Property.objects.filter(
        show_in_front=True, price__lte=1500, price__gt=0
    ).order_by("-featured", "price")[: settings.PROPERTIES_TO_DISPLAY]
    return render(request, "home.html", context={"properties": properties})
