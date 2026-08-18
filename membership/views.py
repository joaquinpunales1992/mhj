import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from membership.models import SavedProperty, SavedSearch
from membership.utils import notify_user_registered_via_email


def show_authenticate_page(request, pk, redirect_to_premium=0):
    return render(
        request,
        "authentication_page.html",
        context={"property_pk": pk, "redirect_to_premium": redirect_to_premium},
    )


def register_via_email(request, pk, redirect_to_premium=0):
    email = request.POST.get("email")
    if email:
        notify_user_registered_via_email(email)

        if redirect_to_premium == "1":
            response = redirect(reverse("upgrade_premium"))
            response.set_cookie("email", email)
            return response
        user_just_registered = 1
        response = redirect(reverse("property_detail", args=[pk, user_just_registered]))
        response.set_cookie("email", email)
        return response


def approved_membership_payment(request):
    # TODO
    pass


# --- Free-account features -------------------------------------------------
# Favourites, saved searches and the analysis blocks on a property page are
# what the free account buys. They all return 401 rather than redirecting to a
# login page, so the JS can show a signup prompt in place without losing the
# user's position on the map.


def _login_required_json(request):
    """Uniform 401 for the fetch-driven endpoints below."""
    return JsonResponse(
        {"error": "login_required", "login_url": reverse("account_login")},
        status=401,
    )


@require_POST
def toggle_saved_property(request):
    """Add or remove a favourite. Returns the resulting state."""
    if not request.user.is_authenticated:
        return _login_required_json(request)

    try:
        property_id = int(json.loads(request.body or "{}").get("property_id"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "property_id required"}, status=400)

    existing = SavedProperty.objects.filter(
        user=request.user, property_id=property_id
    ).first()
    if existing:
        existing.delete()
        saved = False
    else:
        # get_or_create rather than create: a double-click would otherwise trip
        # the unique constraint and 500.
        SavedProperty.objects.get_or_create(
            user=request.user, property_id=property_id
        )
        saved = True

    return JsonResponse(
        {
            "saved": saved,
            "count": request.user.saved_properties.count(),
            # How many people have saved *this* property, for the counter beside
            # the heart on the property page. Counted here rather than derived
            # client-side so the number stays right when several tabs are open.
            "property_saves": SavedProperty.objects.filter(
                property_id=property_id
            ).count(),
        }
    )


@require_POST
def save_search(request):
    """Store the current city/price filter for the signed-in user."""
    if not request.user.is_authenticated:
        return _login_required_json(request)

    data = json.loads(request.body or "{}")
    city = (data.get("city") or "").strip()[:100]
    price = (data.get("price") or "").strip()[:20]

    search, created = SavedSearch.objects.get_or_create(
        user=request.user, city=city, price=price
    )
    return JsonResponse({"created": created, "label": search.label})


@login_required
def saved_view(request):
    """The signed-in user's favourites and saved searches."""
    return render(
        request,
        "saved.html",
        {
            "nav": "saved",
            "saved_properties": (
                SavedProperty.objects.filter(user=request.user)
                .select_related("property")
            ),
            "saved_searches": SavedSearch.objects.filter(user=request.user),
        },
    )


# --- Pro subscription ------------------------------------------------------


@login_required
def upgrade_pro(request):
    """The Pro upgrade page.

    Renders a live PayPal subscribe button when a plan id is configured, and a
    waitlist form when it isn't — so the page is safe to ship before billing
    is set up, and the interest it records tells you whether finishing the
    integration is worth it.
    """
    from django.conf import settings

    subscription = getattr(request.user, "subscription", None)
    return render(
        request,
        "upgrade_pro.html",
        {
            "nav": "pro",
            "pro_price": settings.PRO_PRICE_LABEL,
            "paypal_client_id": settings.PAYPAL_CLIENT_ID,
            "paypal_plan_id": settings.PAYPAL_PLAN_ID,
            "billing_configured": bool(
                settings.PAYPAL_CLIENT_ID and settings.PAYPAL_PLAN_ID
            ),
            "subscription": subscription,
            "already_pro": bool(subscription and subscription.is_active),
            "free_limit": settings.VIEW_LIMIT_FREE,
            "next_url": request.GET.get("next", ""),
        },
    )


@require_POST
def register_subscription(request):
    """Record a subscription the moment PayPal's JS approves it.

    The webhook is the authority on status, but it can lag by seconds and the
    user is standing there expecting access — so store the id immediately and
    let the webhook correct it.
    """
    if not request.user.is_authenticated:
        return _login_required_json(request)

    from membership.models import Subscription

    subscription_id = (json.loads(request.body or "{}").get("subscription_id") or "").strip()
    if not subscription_id:
        return JsonResponse({"error": "subscription_id required"}, status=400)

    Subscription.objects.update_or_create(
        user=request.user,
        defaults={
            "paypal_subscription_id": subscription_id,
            "status": Subscription.STATUS_ACTIVE,
        },
    )
    return JsonResponse({"ok": True})
