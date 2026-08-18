"""One-off PayPal payments, for the paid consultation.

Separate from paypal.py, which owns the *subscription* webhook. Different API
(v2 Orders rather than v1 Billing), different lifecycle: a subscription is a
long-lived thing kept in step by webhooks, while an order is created, approved
by the payer on PayPal, then captured once and finished.

The flow, and why it is shaped this way:

    create_order()   we tell PayPal the amount and where to send the payer back
    -> visitor approves on PayPal
    -> PayPal redirects to the return url with ?token=<order id>
    capture_order()  we take the money and only then confirm the booking

Capture is the moment money actually moves. Nothing is treated as paid before
it, because an approved-but-uncaptured order is a payer who got as far as the
PayPal button and stopped — common enough that treating approval as payment
would hand out free calls.

The amount is always taken from settings here, never from the request: a price
that arrives from the browser is a price the browser can edit.
"""

import logging

import requests
from django.conf import settings

from membership.paypal import _access_token, _api_base

logger = logging.getLogger(__name__)


class PayPalError(Exception):
    """PayPal refused, or could not be reached. Carries a safe-to-log detail."""


def _headers(token, idempotency_key=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        # PayPal replays the original response for a repeated key rather than
        # creating a second order or taking the money twice, which is what makes
        # a double-submit or a retried request safe.
        headers["PayPal-Request-Id"] = idempotency_key
    return headers


def _detail(response):
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300]
    parts = [payload.get("name", ""), payload.get("message", "")]
    for issue in (payload.get("details") or [])[:3]:
        parts.append(f"{issue.get('issue')}: {issue.get('description')}")
    return " | ".join(p for p in parts if p)[:400]


def create_order(*, amount, currency, description, return_url, cancel_url,
                 reference, idempotency_key=None):
    """Create an order and return (order_id, approve_url).

    `reference` goes into custom_id so a captured payment can be traced back to
    the booking row even if our own redirect never happens.
    """
    token = _access_token()
    if not token:
        raise PayPalError("PayPal is not configured, or rejected the credentials.")

    body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {"currency_code": currency, "value": str(amount)},
                "description": description[:127],
                "custom_id": str(reference)[:127],
            }
        ],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    # NO_SHIPPING: this is a phone call, and asking for a
                    # shipping address is both pointless and a reason to abandon.
                    "shipping_preference": "NO_SHIPPING",
                    "user_action": "PAY_NOW",
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                }
            }
        },
    }

    try:
        response = requests.post(
            f"{_api_base()}/v2/checkout/orders",
            json=body,
            headers=_headers(token, idempotency_key),
            timeout=20,
        )
    except Exception as e:
        logger.error("PayPal order request could not be sent: %s", e)
        raise PayPalError("Could not reach PayPal.") from e

    if response.status_code not in (200, 201):
        detail = _detail(response)
        logger.error("PayPal refused to create the order (HTTP %s) — %s",
                     response.status_code, detail)
        raise PayPalError(detail)

    payload = response.json()
    order_id = payload.get("id")
    approve = next(
        (l.get("href") for l in payload.get("links", [])
         if l.get("rel") in ("payer-action", "approve")),
        None,
    )
    if not (order_id and approve):
        logger.error("PayPal order response had no approve link: %s", payload)
        raise PayPalError("PayPal did not return an approval link.")
    return order_id, approve


def capture_order(order_id):
    """Capture an approved order. Returns a dict describing the payment.

    Raises PayPalError if the money did not move. An order already captured
    comes back from PayPal as ORDER_ALREADY_CAPTURED; that is treated as success
    rather than an error, because it means someone reloaded the return url and
    the payment is real either way.
    """
    token = _access_token()
    if not token:
        raise PayPalError("PayPal is not configured, or rejected the credentials.")

    try:
        response = requests.post(
            f"{_api_base()}/v2/checkout/orders/{order_id}/capture",
            json={},
            headers=_headers(token, idempotency_key=f"capture-{order_id}"),
            timeout=20,
        )
    except Exception as e:
        logger.error("PayPal capture could not be sent for %s: %s", order_id, e)
        raise PayPalError("Could not reach PayPal.") from e

    if response.status_code in (200, 201):
        return _summarise(response.json())

    detail = _detail(response)
    if "ORDER_ALREADY_CAPTURED" in detail:
        logger.info("Order %s was already captured; treating as paid.", order_id)
        return _fetch(order_id, token)

    logger.error("PayPal capture failed for %s (HTTP %s) — %s",
                 order_id, response.status_code, detail)
    raise PayPalError(detail)


def _fetch(order_id, token):
    response = requests.get(
        f"{_api_base()}/v2/checkout/orders/{order_id}",
        headers=_headers(token),
        timeout=20,
    )
    if response.status_code != 200:
        raise PayPalError(_detail(response))
    return _summarise(response.json())


def _summarise(payload):
    """Pull the few fields worth storing out of PayPal's envelope."""
    unit = (payload.get("purchase_units") or [{}])[0]
    capture = ((unit.get("payments") or {}).get("captures") or [{}])[0]
    amount = capture.get("amount") or {}
    payer = payload.get("payer") or {}
    return {
        "order_id": payload.get("id", ""),
        "status": payload.get("status", ""),
        "capture_id": capture.get("id", ""),
        "capture_status": capture.get("status", ""),
        "amount": amount.get("value", ""),
        "currency": amount.get("currency_code", ""),
        "reference": unit.get("custom_id", ""),
        "payer_email": payer.get("email_address", ""),
        # COMPLETED on the capture is the only thing that means paid. An order
        # can be COMPLETED overall while a capture is PENDING (a review hold),
        # and that is not money in the account yet.
        "paid": capture.get("status") == "COMPLETED",
    }
