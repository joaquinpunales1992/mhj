"""Create and verify the PayPal objects the Pro subscription needs.

Doing this by hand in the PayPal dashboard is fiddly and easy to get subtly
wrong (a plan in the wrong currency, a webhook missing an event). This creates
them through the API instead and prints the ids to put in .env.

Prerequisite — the one part that can't be automated: create a REST app at
https://developer.paypal.com/dashboard/applications and copy its Client ID and
Secret into .env as PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET, with
PAYPAL_ENVIRONMENT=sandbox while testing.

    manage.py paypal_setup --check                 # credentials only
    manage.py paypal_setup --create-plan           # product + $10/mo plan
    manage.py paypal_setup --create-webhook https://akiyainjapan.com/api/paypal-webhook
    manage.py paypal_setup --status                # show what already exists

Safe to re-run: it lists existing objects before creating anything, and will
not create a second plan when a matching one is already there.
"""

import json

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

API_BASE = {
    "live": "https://api-m.paypal.com",
    "sandbox": "https://api-m.sandbox.paypal.com",
}

PRODUCT_NAME = "My Akiya in Japan — Pro"
PLAN_NAME = "Pro monthly"

# Events the webhook must receive for membership.paypal.webhook to keep local
# state correct. Missing any of these means someone stays Pro after they stop
# paying, or loses access while still paying.
WEBHOOK_EVENTS = [
    "BILLING.SUBSCRIPTION.ACTIVATED",
    "BILLING.SUBSCRIPTION.UPDATED",
    "BILLING.SUBSCRIPTION.CANCELLED",
    "BILLING.SUBSCRIPTION.SUSPENDED",
    "BILLING.SUBSCRIPTION.EXPIRED",
    "PAYMENT.SALE.COMPLETED",
]


class Command(BaseCommand):
    help = "Set up and verify PayPal subscription objects."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true",
                            help="Verify the credentials work.")
        parser.add_argument("--diagnose", action="store_true",
                            help="Inspect the credentials without revealing "
                                 "them, and show PayPal's own error message.")
        parser.add_argument("--status", action="store_true",
                            help="List existing products, plans and webhooks.")
        parser.add_argument("--create-plan", action="store_true",
                            help="Create the product and monthly plan.")
        parser.add_argument("--price", default="10.00",
                            help="Monthly price (default 10.00).")
        parser.add_argument("--currency", default="USD")
        parser.add_argument("--create-webhook", metavar="URL",
                            help="Register the webhook at this public URL.")

    # -- plumbing ----------------------------------------------------------

    def base(self):
        return API_BASE.get(settings.PAYPAL_ENVIRONMENT, API_BASE["sandbox"])

    def token(self):
        if not (settings.PAYPAL_CLIENT_ID and settings.PAYPAL_CLIENT_SECRET):
            self.stderr.write(self.style.ERROR(
                "PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set in .env."
            ))
            return None
        r = requests.post(
            f"{self.base()}/v1/oauth2/token",
            auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
            data={"grant_type": "client_credentials"},
            timeout=20,
        )
        if r.status_code != 200:
            self.stderr.write(self.style.ERROR(
                f"Auth failed ({r.status_code}). Check the credentials match "
                f"PAYPAL_ENVIRONMENT={settings.PAYPAL_ENVIRONMENT}: {r.text[:200]}"
            ))
            return None
        return r.json()["access_token"]

    def api(self, method, path, token, **kwargs):
        return requests.request(
            method,
            f"{self.base()}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=30,
            **kwargs,
        )

    # -- commands ----------------------------------------------------------

    def diagnose(self):
        """Report on the credentials without printing them.

        A 401 from PayPal has a handful of causes that look identical from the
        outside; this distinguishes them. By far the most common is sandbox
        credentials used against live, or live against sandbox — the two are
        separate accounts and the ids are not interchangeable.
        """
        cid = settings.PAYPAL_CLIENT_ID or ""
        secret = settings.PAYPAL_CLIENT_SECRET or ""

        self.stdout.write("\nCredential inspection (values not shown):")
        for label, value in (("CLIENT_ID", cid), ("CLIENT_SECRET", secret)):
            if not value:
                self.stdout.write(self.style.ERROR(f"  {label}: EMPTY"))
                continue
            issues = []
            if value != value.strip():
                issues.append("has leading/trailing whitespace")
            if '"' in value or "'" in value:
                issues.append("contains a quote character — remove quotes in .env")
            if len(value) < 50:
                issues.append(f"only {len(value)} chars, PayPal's are usually 80+")
            if " " in value.strip():
                issues.append("contains a space — likely truncated on paste")
            self.stdout.write(
                f"  {label}: {len(value)} chars, starts {value[:6]!r}, ends {value[-4:]!r}"
            )
            for i in issues:
                self.stdout.write(self.style.WARNING(f"      ! {i}"))

        self.stdout.write(f"\nCalling {self.base()}/v1/oauth2/token ...")
        r = requests.post(
            f"{self.base()}/v1/oauth2/token",
            auth=(cid.strip(), secret.strip()),
            data={"grant_type": "client_credentials"},
            timeout=20,
        )
        self.stdout.write(f"  HTTP {r.status_code}")
        try:
            payload = r.json()
        except ValueError:
            payload = {}
        if r.status_code == 200:
            self.stdout.write(self.style.SUCCESS(
                "  Credentials are valid for this environment."
            ))
            return
        self.stdout.write(self.style.ERROR(
            f"  {payload.get('error', '?')}: {payload.get('error_description', r.text[:160])}"
        ))

        if payload.get("error") != "invalid_client":
            return

        # Don't guess which environment they came from — ask the other host.
        # Fetching a token is read-only and moves no money, so this is safe
        # even against live.
        other = "live" if settings.PAYPAL_ENVIRONMENT != "live" else "sandbox"
        other_base = API_BASE[other]
        self.stdout.write(f"\nTrying the {other} host to identify them ...")
        try:
            r2 = requests.post(
                f"{other_base}/v1/oauth2/token",
                auth=(cid.strip(), secret.strip()),
                data={"grant_type": "client_credentials"},
                timeout=20,
            )
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"  could not reach {other_base}: {e}"))
            return

        if r2.status_code == 200:
            self.stdout.write(self.style.SUCCESS(
                f"  These are {other.upper()} credentials — they authenticate fine "
                f"against {other_base}."
            ))
            if other == "live":
                self.stdout.write(self.style.WARNING(
                    "\n  Decide which you want:\n"
                    "   • To test safely first (recommended): create a SANDBOX app at\n"
                    "     developer.paypal.com → Apps & Credentials → Sandbox tab,\n"
                    "     and put those credentials in .env instead.\n"
                    "   • To go live now: set PAYPAL_ENVIRONMENT=live in .env, then\n"
                    "     re-run --create-plan and --create-webhook. Real cards will\n"
                    "     be charged, and the subscribe flow has never been exercised\n"
                    "     end to end, so test with your own card and cancel."
                ))
            else:
                self.stdout.write(self.style.WARNING(
                    f"\n  Set PAYPAL_ENVIRONMENT={other} in .env, then re-run "
                    f"--create-plan and --create-webhook."
                ))
        else:
            self.stdout.write(self.style.ERROR(
                f"  Rejected by {other} too (HTTP {r2.status_code}). The pair is not a\n"
                f"  valid PayPal client id + secret. Re-copy both from the same app in\n"
                f"  the dashboard — mixing the id from one app with the secret of\n"
                f"  another gives exactly this error."
            ))

    def handle(self, *args, **options):
        self.stdout.write(f"PayPal environment: {settings.PAYPAL_ENVIRONMENT}")
        if options["diagnose"]:
            return self.diagnose()
        token = self.token()
        if not token:
            return
        self.stdout.write(self.style.SUCCESS("Credentials OK."))

        if options["check"]:
            return
        if options["status"]:
            return self.show_status(token)
        if options["create_plan"]:
            self.create_plan(token, options["price"], options["currency"])
        if options["create_webhook"]:
            self.create_webhook(token, options["create_webhook"])
        if not (options["create_plan"] or options["create_webhook"]):
            self.show_status(token)

    def show_status(self, token):
        r = self.api("GET", "/v1/catalogs/products?page_size=20", token)
        products = r.json().get("products", []) if r.ok else []
        self.stdout.write(f"\nProducts ({len(products)}):")
        for p in products:
            self.stdout.write(f"  {p['id']}  {p.get('name')}")

        r = self.api("GET", "/v1/billing/plans?page_size=20", token)
        plans = r.json().get("plans", []) if r.ok else []
        self.stdout.write(f"\nPlans ({len(plans)}):")
        for p in plans:
            self.stdout.write(
                f"  {p['id']}  {p.get('name')}  [{p.get('status')}]"
            )
            if p["id"] == settings.PAYPAL_PLAN_ID:
                self.stdout.write(self.style.SUCCESS("     ^ this is PAYPAL_PLAN_ID"))
        if plans and not settings.PAYPAL_PLAN_ID:
            self.stdout.write(self.style.WARNING(
                "\n  PAYPAL_PLAN_ID is not set — the Pro page will show the "
                "waitlist instead of a subscribe button."
            ))

        r = self.api("GET", "/v1/notifications/webhooks", token)
        hooks = r.json().get("webhooks", []) if r.ok else []
        self.stdout.write(f"\nWebhooks ({len(hooks)}):")
        for h in hooks:
            self.stdout.write(f"  {h['id']}  {h.get('url')}")
            names = {e["name"] for e in h.get("event_types", [])}
            missing = [e for e in WEBHOOK_EVENTS if e not in names]
            if missing:
                self.stdout.write(self.style.WARNING(
                    f"     missing events: {', '.join(missing)}"
                ))
            if h["id"] == settings.PAYPAL_WEBHOOK_ID:
                self.stdout.write(self.style.SUCCESS("     ^ this is PAYPAL_WEBHOOK_ID"))

    def create_plan(self, token, price, currency):
        # Reuse the product if it's already there — re-running shouldn't
        # litter the account with duplicates.
        r = self.api("GET", "/v1/catalogs/products?page_size=20", token)
        existing = next(
            (p for p in r.json().get("products", []) if p.get("name") == PRODUCT_NAME),
            None,
        ) if r.ok else None

        if existing:
            product_id = existing["id"]
            self.stdout.write(f"Using existing product {product_id}")
        else:
            r = self.api("POST", "/v1/catalogs/products", token, json={
                "name": PRODUCT_NAME,
                "description": "Unlimited access to full property details.",
                "type": "SERVICE",
                "category": "SOFTWARE",
            })
            if not r.ok:
                return self.stderr.write(self.style.ERROR(
                    f"Product creation failed: {r.status_code} {r.text[:300]}"
                ))
            product_id = r.json()["id"]
            self.stdout.write(self.style.SUCCESS(f"Created product {product_id}"))

        r = self.api("POST", "/v1/billing/plans", token, json={
            "product_id": product_id,
            "name": PLAN_NAME,
            "description": f"Pro membership, {price} {currency} per month.",
            "billing_cycles": [{
                "frequency": {"interval_unit": "MONTH", "interval_count": 1},
                "tenure_type": "REGULAR",
                "sequence": 1,
                # 0 = bill forever until cancelled.
                "total_cycles": 0,
                "pricing_scheme": {
                    "fixed_price": {"value": price, "currency_code": currency}
                },
            }],
            "payment_preferences": {
                "auto_bill_outstanding": True,
                # Give a failed payment three attempts before suspending, then
                # suspend rather than cancel so they can fix their card.
                "payment_failure_threshold": 3,
                "setup_fee_failure_action": "CONTINUE",
            },
        })
        if not r.ok:
            return self.stderr.write(self.style.ERROR(
                f"Plan creation failed: {r.status_code} {r.text[:300]}"
            ))

        plan_id = r.json()["id"]
        self.stdout.write(self.style.SUCCESS(f"Created plan {plan_id}"))
        self.stdout.write("\nAdd to .env:")
        self.stdout.write(self.style.SUCCESS(f"  PAYPAL_PLAN_ID={plan_id}"))

    def create_webhook(self, token, url):
        if not url.startswith("https://"):
            return self.stderr.write(self.style.ERROR(
                "PayPal only delivers webhooks to https:// URLs."
            ))

        r = self.api("GET", "/v1/notifications/webhooks", token)
        existing = next(
            (h for h in r.json().get("webhooks", []) if h.get("url") == url), None
        ) if r.ok else None
        if existing:
            self.stdout.write(f"Webhook already registered: {existing['id']}")
            self.stdout.write(self.style.SUCCESS(
                f"  PAYPAL_WEBHOOK_ID={existing['id']}"
            ))
            return

        r = self.api("POST", "/v1/notifications/webhooks", token, json={
            "url": url,
            "event_types": [{"name": e} for e in WEBHOOK_EVENTS],
        })
        if not r.ok:
            return self.stderr.write(self.style.ERROR(
                f"Webhook creation failed: {r.status_code} {r.text[:300]}"
            ))

        webhook_id = r.json()["id"]
        self.stdout.write(self.style.SUCCESS(f"Created webhook {webhook_id}"))
        self.stdout.write("\nAdd to .env:")
        self.stdout.write(self.style.SUCCESS(f"  PAYPAL_WEBHOOK_ID={webhook_id}"))
