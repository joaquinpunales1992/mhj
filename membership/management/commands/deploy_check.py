"""Verify a deployment is actually complete and correctly configured.

Written because a deploy here has several independent moving parts — code
pulled, migrations applied, cities geocoded, PayPal configured, DEBUG off — and
a half-finished one fails quietly: the map renders empty, or /pro/ shows a
waitlist, with nothing in the logs to say why.

    manage.py deploy_check

Exits non-zero if anything is broken, so it can gate a deploy script.
"""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.migrations.executor import MigrationExecutor
from django.db import connection


class Command(BaseCommand):
    help = "Check that this deployment is complete and configured."

    def handle(self, *args, **options):
        self.problems = []
        self.warnings = []

        self.check_code()
        self.check_migrations()
        self.check_geocoding()
        self.check_prices()
        self.check_paypal()
        self.check_consultation()
        self.check_security()

        self.stdout.write("")
        if self.problems:
            self.stdout.write(self.style.ERROR(f"{len(self.problems)} problem(s):"))
            for p in self.problems:
                self.stdout.write(self.style.ERROR(f"  ✗ {p}"))
        if self.warnings:
            self.stdout.write(self.style.WARNING(f"{len(self.warnings)} warning(s):"))
            for w in self.warnings:
                self.stdout.write(self.style.WARNING(f"  ! {w}"))
        if not self.problems and not self.warnings:
            self.stdout.write(self.style.SUCCESS("Everything checks out."))
        if self.problems:
            raise SystemExit(1)

    def ok(self, message):
        self.stdout.write(self.style.SUCCESS(f"  ✓ {message}"))

    # -- checks ------------------------------------------------------------

    def check_code(self):
        self.stdout.write("Code")
        try:
            from inventory.models import GeocodedPlace  # noqa: F401
            from membership.models import Subscription, PropertyView  # noqa: F401
            from membership.metering import check_access  # noqa: F401
            self.ok("new modules import (GeocodedPlace, Subscription, metering)")
        except ImportError as e:
            self.problems.append(
                f"Code is stale — {e}. The git pull did not land. Tracked .pyc "
                f"files make pull fail; run: git checkout -- '*__pycache__*' && git pull"
            )

    def check_migrations(self):
        self.stdout.write("Migrations")
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if plan:
            names = ", ".join(f"{m.app_label}.{m.name}" for m, _ in plan[:5])
            self.problems.append(f"{len(plan)} migration(s) unapplied: {names}")
        else:
            self.ok("all migrations applied")

    def check_geocoding(self):
        self.stdout.write("Map data")
        try:
            from inventory.models import GeocodedPlace, Property
            from inventory.utils import city_key
        except ImportError:
            return  # already reported by check_code

        located = GeocodedPlace.objects.exclude(latitude__isnull=True).count()
        total = GeocodedPlace.objects.count()
        if total == 0:
            self.problems.append(
                "No cities geocoded — the map will be empty. Run: "
                "manage.py geocode_places"
            )
            return

        keys = {
            k for k in (
                city_key(loc) for loc in
                Property.objects.filter(show_in_front=True)
                .exclude(location="").values_list("location", flat=True)
            ) if k
        }
        missing = len(keys) - total
        self.ok(f"{located}/{total} cities located")
        if missing > 0:
            self.warnings.append(
                f"{missing} city key(s) never attempted — run geocode_places again "
                f"(new properties add new cities)"
            )
        if total and located / total < 0.8:
            self.warnings.append(
                f"only {100 * located // total}% of cities resolved; try "
                f"geocode_places --retry"
            )

    def check_prices(self):
        self.stdout.write("Data quality")
        from inventory.models import Property

        suspect = Property.objects.filter(
            price__gt=0, price__lt=50, show_in_front=True
        ).count()
        if suspect:
            self.warnings.append(
                f"{suspect} visible propert(ies) priced under 50万 (~$3.5k) — "
                f"almost certainly bad scrapes. Run: manage.py repair_prices --dry-run"
            )
        else:
            self.ok("no implausibly cheap properties on show")

    def check_paypal(self):
        self.stdout.write("PayPal")
        cid = settings.PAYPAL_CLIENT_ID
        secret = settings.PAYPAL_CLIENT_SECRET
        plan = settings.PAYPAL_PLAN_ID
        hook = settings.PAYPAL_WEBHOOK_ID

        if not (cid and secret):
            self.warnings.append(
                "PayPal credentials not set — /pro/ shows the waitlist. Fine "
                "until you want to charge."
            )
            return
        self.ok(f"credentials present ({settings.PAYPAL_ENVIRONMENT})")

        if not plan:
            self.problems.append(
                "PAYPAL_CLIENT_ID is set but PAYPAL_PLAN_ID is not — /pro/ will "
                "still show the waitlist. Run: manage.py paypal_setup --create-plan"
            )
        else:
            self.ok(f"plan configured ({plan})")

        if not hook:
            self.problems.append(
                "PAYPAL_WEBHOOK_ID is not set. Without it the webhook refuses "
                "every event, so cancellations and renewals are never recorded — "
                "people would keep Pro after they stop paying. Run: "
                "manage.py paypal_setup --create-webhook <https url>"
            )
        else:
            self.ok(f"webhook id configured ({hook})")

        if settings.PAYPAL_ENVIRONMENT == "sandbox":
            self.warnings.append(
                "PAYPAL_ENVIRONMENT=sandbox — real customers cannot subscribe. "
                "Switch to live once tested (and re-create plan + webhook)."
            )

        # Live credentials against a live plan is the combination that takes
        # real money, so confirm the token actually works.
        from membership.paypal import _access_token
        if _access_token():
            self.ok("PayPal API reachable and credentials valid")
        else:
            self.problems.append(
                "PayPal rejected the credentials, or the API is unreachable. "
                "Check they match PAYPAL_ENVIRONMENT."
            )

    def check_consultation(self):
        self.stdout.write("Consultation")
        if settings.CONSULT_BOOKING_URL:
            self.ok(f"booking link set ({settings.CONSULT_BOOKING_URL})")
        else:
            self.warnings.append(
                "CONSULT_BOOKING_URL is empty — /consultation/ shows the enquiry "
                "fallback instead of a booking button, so no calls can be sold."
            )

    def check_security(self):
        self.stdout.write("Security")
        if settings.DEBUG:
            self.problems.append(
                "DEBUG=True. Error pages leak stack traces and SQL to visitors, "
                "and Django retains every query for the process lifetime — a "
                "memory leak on a low-RAM box. Set DEBUG=False in .env."
            )
        else:
            self.ok("DEBUG is off")

        if settings.SECRET_KEY.startswith("django-insecure-"):
            self.problems.append(
                "SECRET_KEY is still the committed default, which anyone with "
                "repo access can use to forge session cookies. Set a fresh one "
                "in .env (this logs everyone out, so do it early)."
            )
        else:
            self.ok("SECRET_KEY has been rotated")

        if not settings.SESSION_COOKIE_SECURE:
            self.warnings.append(
                "SESSION_COOKIE_SECURE=False — session cookies may travel over "
                "plain HTTP. Set it True if the site is HTTPS-only."
            )
        else:
            self.ok("secure session cookies")
