"""Ask PayPal whether this deployment's billing is actually set up.

The upgrade page's button opens a PayPal window, and when something is wrong
that window is blank — PayPal refuses in its own popup, so nothing reaches the
browser console and onError never fires. There was no way to tell whether the
cause was the client id, the plan, or the environment, short of clicking it.

Everything this checks is a thing that produces exactly that blank window.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from membership.paypal import _access_token, _api_base

import requests


def mask(value):
    if not value:
        return "(unset)"
    return f"{value[:6]}…{value[-4:]} ({len(value)} chars)"


class Command(BaseCommand):
    help = "Check the PayPal client id, secret, plan and environment agree."

    def handle(self, *args, **options):
        ok = self.style.SUCCESS
        warn = self.style.WARNING
        bad = self.style.ERROR

        env = settings.PAYPAL_ENVIRONMENT
        base = _api_base()
        self.stdout.write(f"Environment:  {env}")
        self.stdout.write(f"API base:     {base}")
        self.stdout.write(f"Client id:    {mask(settings.PAYPAL_CLIENT_ID)}")
        self.stdout.write(f"Client secret:{' set' if settings.PAYPAL_CLIENT_SECRET else ' (unset)'}")
        self.stdout.write(f"Plan id:      {settings.PAYPAL_PLAN_ID or '(unset)'}")
        self.stdout.write(f"Webhook id:   {settings.PAYPAL_WEBHOOK_ID or '(unset)'}")
        self.stdout.write("")

        # The button is only rendered when both of these are set, so an unset
        # one is not a broken button — it is no button at all.
        if not (settings.PAYPAL_CLIENT_ID and settings.PAYPAL_PLAN_ID):
            self.stdout.write(warn(
                "PAYPAL_CLIENT_ID and PAYPAL_PLAN_ID must both be set or the "
                "upgrade page shows the waitlist instead of a button."
            ))
            return

        token = _access_token()
        if not token:
            self.stdout.write(bad(
                f"Could not get a token from {base}. The log line above has "
                "PayPal's own reason. 'invalid_client' almost always means the "
                f"credentials belong to the other environment — they are being "
                f"sent to the {env} host."
            ))
            return
        self.stdout.write(ok(f"Credentials accepted by {base}."))

        # The browser SDK picks its environment from the client id alone. The
        # server picks its host from PAYPAL_ENVIRONMENT. A plan that exists in
        # one and not the other is the blank window.
        try:
            response = requests.get(
                f"{base}/v1/billing/plans/{settings.PAYPAL_PLAN_ID}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
        except Exception as exc:
            self.stdout.write(bad(f"Could not reach {base}: {exc}"))
            return

        if response.status_code == 404:
            self.stdout.write(bad(
                f"Plan {settings.PAYPAL_PLAN_ID} does not exist in {env}. "
                "This is what a blank PayPal window looks like: the button is "
                "rendered by a client id from one environment and asks for a "
                "plan that lives in the other. Check both come from the same "
                "PayPal app."
            ))
            return
        if response.status_code != 200:
            self.stdout.write(bad(
                f"Plan lookup failed (HTTP {response.status_code}): "
                f"{response.text[:300]}"
            ))
            return

        plan = response.json()
        status = plan.get("status")
        self.stdout.write(f"Plan name:    {plan.get('name')}")
        self.stdout.write(f"Plan status:  {status}")
        for cycle in plan.get("billing_cycles") or []:
            price = (cycle.get("pricing_scheme") or {}).get("fixed_price") or {}
            if price:
                self.stdout.write(
                    f"Plan price:   {price.get('value')} {price.get('currency_code')}"
                )

        if status == "ACTIVE":
            self.stdout.write(ok(
                "Billing is configured and the plan is active. A blank window "
                "after this is a browser problem — an extension blocking the "
                "popup, or third-party cookies turned off."
            ))
        else:
            self.stdout.write(bad(
                f"The plan is {status}, not ACTIVE. PayPal will not open a "
                "subscription window for an inactive plan."
            ))
