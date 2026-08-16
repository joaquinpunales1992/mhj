"""PayPal subscription webhook.

PayPal is the source of truth for whether someone is paying; this endpoint
keeps the local Subscription row in step so the site can gate on a single
boolean instead of calling PayPal on every request.

Events handled:

    BILLING.SUBSCRIPTION.ACTIVATED   -> access on
    BILLING.SUBSCRIPTION.UPDATED     -> status refreshed
    BILLING.SUBSCRIPTION.CANCELLED   -> access until the paid period ends
    BILLING.SUBSCRIPTION.SUSPENDED   -> ditto (usually a failed payment)
    BILLING.SUBSCRIPTION.EXPIRED     -> access off
    PAYMENT.SALE.COMPLETED           -> renewal; extend the period

Every webhook is verified with PayPal before it is acted on. Without that,
anyone who knows the URL could POST a forged "ACTIVATED" and get Pro for free.
"""

import json
import logging

import requests
from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

API_BASE = {
    "live": "https://api-m.paypal.com",
    "sandbox": "https://api-m.sandbox.paypal.com",
}


def _api_base():
    return API_BASE.get(settings.PAYPAL_ENVIRONMENT, API_BASE["sandbox"])


def _access_token():
    """OAuth token for server-to-server calls. None if unconfigured."""
    if not (settings.PAYPAL_CLIENT_ID and settings.PAYPAL_CLIENT_SECRET):
        return None
    try:
        response = requests.post(
            f"{_api_base()}/v1/oauth2/token",
            auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
            data={"grant_type": "client_credentials"},
            timeout=15,
        )
        response.raise_for_status()
        return response.json().get("access_token")
    except Exception as e:
        logger.error("PayPal token request failed: %s", e)
        return None


def _verify(request, body):
    """Ask PayPal whether this webhook really came from them.

    Returns False on any doubt — an unverifiable event is treated as hostile
    rather than trusted, because acting on a forged one grants free access.
    """
    if not settings.PAYPAL_WEBHOOK_ID:
        logger.error("PAYPAL_WEBHOOK_ID is not set; refusing webhook.")
        return False

    token = _access_token()
    if not token:
        return False

    payload = {
        "auth_algo": request.headers.get("Paypal-Auth-Algo", ""),
        "cert_url": request.headers.get("Paypal-Cert-Url", ""),
        "transmission_id": request.headers.get("Paypal-Transmission-Id", ""),
        "transmission_sig": request.headers.get("Paypal-Transmission-Sig", ""),
        "transmission_time": request.headers.get("Paypal-Transmission-Time", ""),
        "webhook_id": settings.PAYPAL_WEBHOOK_ID,
        "webhook_event": body,
    }
    try:
        response = requests.post(
            f"{_api_base()}/v1/notifications/verify-webhook-signature",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        response.raise_for_status()
        return response.json().get("verification_status") == "SUCCESS"
    except Exception as e:
        logger.error("PayPal webhook verification failed: %s", e)
        return False


@csrf_exempt
@require_POST
def webhook(request):
    from membership.models import Subscription

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponse("bad json", status=400)

    if not _verify(request, body):
        return HttpResponseForbidden("unverified")

    event = body.get("event_type", "")
    resource = body.get("resource", {}) or {}

    # Renewals arrive as a sale, where the subscription id is billing_agreement_id.
    subscription_id = resource.get("id") or resource.get("billing_agreement_id")
    if event == "PAYMENT.SALE.COMPLETED":
        subscription_id = resource.get("billing_agreement_id")

    if not subscription_id:
        logger.info("PayPal webhook %s carried no subscription id; ignored.", event)
        return HttpResponse("ignored")

    subscription = Subscription.objects.filter(
        paypal_subscription_id=subscription_id
    ).first()
    if not subscription:
        # Can happen if the browser call that registers the id didn't complete.
        # Log loudly: it means someone is paying and not getting access.
        logger.error(
            "PayPal webhook %s for unknown subscription %s", event, subscription_id
        )
        return HttpResponse("unknown subscription")

    if event == "BILLING.SUBSCRIPTION.ACTIVATED":
        subscription.status = Subscription.STATUS_ACTIVE
    elif event == "BILLING.SUBSCRIPTION.CANCELLED":
        subscription.status = Subscription.STATUS_CANCELLED
    elif event == "BILLING.SUBSCRIPTION.SUSPENDED":
        subscription.status = Subscription.STATUS_SUSPENDED
    elif event == "BILLING.SUBSCRIPTION.EXPIRED":
        subscription.status = Subscription.STATUS_EXPIRED
        subscription.current_period_end = timezone.now()
    elif event in ("BILLING.SUBSCRIPTION.UPDATED", "BILLING.SUBSCRIPTION.RE-ACTIVATED"):
        status = resource.get("status")
        if status in dict(Subscription.STATUS_CHOICES):
            subscription.status = status
    elif event == "PAYMENT.SALE.COMPLETED":
        subscription.status = Subscription.STATUS_ACTIVE

    # Carry PayPal's own next-billing date when it sends one; it's more
    # accurate than anything computed here.
    next_billing = (resource.get("billing_info") or {}).get("next_billing_time")
    if next_billing:
        parsed = timezone.datetime.fromisoformat(next_billing.replace("Z", "+00:00"))
        subscription.current_period_end = parsed

    subscription.save()
    logger.info(
        "PayPal %s -> subscription %s now %s",
        event,
        subscription_id,
        subscription.status,
    )
    return HttpResponse("ok")
