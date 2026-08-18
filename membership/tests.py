"""Tests for the parts of billing and metering that decide who gets access.

The webhook can't be exercised end to end locally — PayPal only delivers to a
public https:// URL — so signature verification is mocked and the state machine
underneath is tested directly. That machine is what actually grants and revokes
Pro, so it's the part worth covering: a wrong transition either gives away
access for free or locks out someone who is paying.
"""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from inventory.models import Property
from membership.metering import check_access
from membership.models import PropertyView, Subscription

WEBHOOK_URL = "/api/paypal-webhook"


def event(event_type, subscription_id="I-TEST123", **resource):
    body = {"event_type": event_type, "resource": {"id": subscription_id, **resource}}
    return json.dumps(body)


@override_settings(ALLOWED_HOSTS=["testserver"], PAYPAL_WEBHOOK_ID="WH-TEST")
class WebhookTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("sub@example.com", email="sub@example.com")
        self.sub = Subscription.objects.create(
            user=self.user,
            paypal_subscription_id="I-TEST123",
            status=Subscription.STATUS_APPROVAL_PENDING,
        )
        self.client = Client()

    def post(self, payload):
        return self.client.post(
            WEBHOOK_URL, payload, content_type="application/json"
        )

    # -- security ---------------------------------------------------------

    def test_unverified_event_is_rejected(self):
        """The whole scheme rests on this: without verification anyone who
        knows the URL could forge an ACTIVATED event and get Pro free."""
        with patch("membership.paypal._verify", return_value=False):
            response = self.post(event("BILLING.SUBSCRIPTION.ACTIVATED"))
        self.assertEqual(response.status_code, 403)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.STATUS_APPROVAL_PENDING)

    def test_missing_webhook_id_refuses(self):
        """An unconfigured PAYPAL_WEBHOOK_ID must fail closed, not open."""
        with override_settings(PAYPAL_WEBHOOK_ID=""):
            response = self.post(event("BILLING.SUBSCRIPTION.ACTIVATED"))
        self.assertEqual(response.status_code, 403)

    def test_malformed_json(self):
        with patch("membership.paypal._verify", return_value=True):
            response = self.post("not json")
        self.assertEqual(response.status_code, 400)

    # -- state transitions ------------------------------------------------

    def test_activated_grants_access(self):
        with patch("membership.paypal._verify", return_value=True):
            self.post(event("BILLING.SUBSCRIPTION.ACTIVATED"))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.STATUS_ACTIVE)
        self.assertTrue(self.sub.is_active)

    def test_cancelled_keeps_access_until_period_end(self):
        """They paid for the month; taking it away immediately invites a
        dispute and is simply wrong."""
        future = timezone.now() + timedelta(days=12)
        with patch("membership.paypal._verify", return_value=True):
            self.post(event(
                "BILLING.SUBSCRIPTION.CANCELLED",
                billing_info={"next_billing_time": future.isoformat().replace("+00:00", "Z")},
            ))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.STATUS_CANCELLED)
        self.assertTrue(self.sub.is_active, "cancelled but still inside paid period")

    def test_cancelled_without_period_end_revokes(self):
        with patch("membership.paypal._verify", return_value=True):
            self.post(event("BILLING.SUBSCRIPTION.CANCELLED"))
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.is_active)

    def test_expired_revokes_immediately(self):
        self.sub.status = Subscription.STATUS_ACTIVE
        self.sub.current_period_end = timezone.now() + timedelta(days=20)
        self.sub.save()
        with patch("membership.paypal._verify", return_value=True):
            self.post(event("BILLING.SUBSCRIPTION.EXPIRED"))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.STATUS_EXPIRED)
        self.assertFalse(self.sub.is_active)

    def test_suspended_keeps_access_until_period_end(self):
        """Suspension is usually a failed card. Don't punish them mid-period."""
        future = timezone.now() + timedelta(days=5)
        self.sub.current_period_end = future
        self.sub.save()
        with patch("membership.paypal._verify", return_value=True):
            self.post(event("BILLING.SUBSCRIPTION.SUSPENDED"))
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.is_active)

    def test_renewal_reactivates_and_extends(self):
        """Renewals arrive as PAYMENT.SALE.COMPLETED, where the subscription id
        lives in billing_agreement_id rather than id."""
        self.sub.status = Subscription.STATUS_SUSPENDED
        self.sub.save()
        future = timezone.now() + timedelta(days=30)
        payload = json.dumps({
            "event_type": "PAYMENT.SALE.COMPLETED",
            "resource": {
                "id": "SALE-1",
                "billing_agreement_id": "I-TEST123",
                "billing_info": {
                    "next_billing_time": future.isoformat().replace("+00:00", "Z")
                },
            },
        })
        with patch("membership.paypal._verify", return_value=True):
            self.post(payload)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subscription.STATUS_ACTIVE)
        self.assertTrue(self.sub.is_active)

    def test_unknown_subscription_does_not_crash(self):
        with patch("membership.paypal._verify", return_value=True):
            response = self.post(event("BILLING.SUBSCRIPTION.ACTIVATED", "I-NOPE"))
        self.assertEqual(response.status_code, 200)


@override_settings(
    ALLOWED_HOSTS=["testserver"], VIEW_LIMIT_ANONYMOUS=2, VIEW_LIMIT_FREE=3
)
class FieldTierTests(TestCase):
    """Quota and field access are separate axes; these cover the field axis.

    Everyone — anonymous included, and crawlers — sees every open field on the
    properties they may open. Only the premium analysis is Pro-only. Getting
    this wrong either gives away the reason to subscribe or withholds the facts
    a listing is useless without (and that Google ranks the page on).
    """

    def _request(self, user=None):
        from django.test import RequestFactory
        from django.contrib.auth.models import AnonymousUser
        from django.contrib.sessions.middleware import SessionMiddleware

        request = RequestFactory().get("/", HTTP_USER_AGENT="Mozilla/5.0")
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request.user = user or AnonymousUser()
        return request

    def setUp(self):
        self.prop = Property.objects.create(
            url="http://x/tier", price=1000, show_in_front=True,
            location="Oita City, Oita Prefecture",
        )

    def test_anonymous_gets_the_open_fields_but_not_premium(self):
        access = check_access(self._request(), self.prop.pk)
        self.assertFalse(access["premium"])
        self.assertNotIn("standard", access, "the middle field tier is gone")

    def test_crawler_gets_the_open_fields(self):
        """Crawlers resolve to the anonymous tier, so this is what Googlebot
        indexes. A middle tier here silently stopped the areas being indexed."""
        request = self._request()
        request.META["HTTP_USER_AGENT"] = "Mozilla/5.0 (compatible; Googlebot/2.1)"
        access = check_access(request, self.prop.pk)
        self.assertFalse(access["locked"])
        self.assertFalse(access["premium"], "the paid analysis is still not published")

    def test_free_account_gets_no_extra_fields_only_more_views(self):
        """A free account buys quota, not fields: the only thing it adds over
        anonymous is the higher allowance."""
        user = User.objects.create_user("t@example.com", email="t@example.com")
        access = check_access(self._request(user), self.prop.pk)
        self.assertFalse(access["premium"], "price/m², rental, land rights are Pro-only")
        self.assertGreater(access["limit"], check_access(self._request(), self.prop.pk)["limit"])

    def test_pro_gets_everything(self):
        user = User.objects.create_user("p@example.com", email="p@example.com")
        Subscription.objects.create(
            user=user, paypal_subscription_id="I-TIER",
            status=Subscription.STATUS_ACTIVE,
            current_period_end=timezone.now() + timedelta(days=30),
        )
        user.refresh_from_db()
        access = check_access(self._request(user), self.prop.pk)
        self.assertTrue(access["premium"])

    def test_spending_the_quota_does_not_remove_field_access(self):
        """A free account that runs out of views is walled on the next new
        property, but nothing is retroactively taken away."""
        user = User.objects.create_user("q@example.com", email="q@example.com")
        request = self._request(user)
        others = [
            Property.objects.create(url=f"http://x/q{i}", price=1000,
                                    show_in_front=True, location="Oita City, Oita Prefecture")
            for i in range(4)
        ]
        for p in others:
            check_access(request, p.pk)
        access = check_access(request, self.prop.pk)
        self.assertTrue(access["locked"], "quota should be spent")
        self.assertFalse(access["premium"])


@override_settings(
    ALLOWED_HOSTS=["testserver"], VIEW_LIMIT_ANONYMOUS=2, VIEW_LIMIT_FREE=3
)
class MeteringTests(TestCase):
    """The meter and the subscription meet here: an active subscription must
    lift the cap, and an inactive one must not."""

    def setUp(self):
        self.props = [
            Property.objects.create(url=f"http://x/{i}", price=1000, show_in_front=True,
                                    location="Oita City, Oita Prefecture")
            for i in range(6)
        ]
        self.factory_client = Client()

    def _request(self, user=None, ua="Mozilla/5.0"):
        from django.test import RequestFactory
        from django.contrib.auth.models import AnonymousUser
        from django.contrib.sessions.middleware import SessionMiddleware

        request = RequestFactory().get("/", HTTP_USER_AGENT=ua)
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request.user = user or AnonymousUser()
        return request

    def test_anonymous_locks_after_limit(self):
        request = self._request()
        results = [check_access(request, p.pk)["locked"] for p in self.props[:4]]
        self.assertEqual(results, [False, False, True, True])

    def test_revisit_never_costs_a_view(self):
        request = self._request()
        check_access(request, self.props[0].pk)
        check_access(request, self.props[1].pk)
        # Limit reached, but an already-seen property stays open.
        self.assertFalse(check_access(request, self.props[0].pk)["locked"])
        self.assertTrue(check_access(request, self.props[2].pk)["locked"])

    def test_crawler_is_never_metered(self):
        request = self._request(ua="Mozilla/5.0 (compatible; Googlebot/2.1)")
        results = [check_access(request, p.pk)["locked"] for p in self.props]
        self.assertEqual(results, [False] * 6)

    def test_free_account_gets_the_higher_limit(self):
        user = User.objects.create_user("free@example.com", email="free@example.com")
        request = self._request(user=user)
        results = [check_access(request, p.pk)["locked"] for p in self.props[:5]]
        self.assertEqual(results, [False, False, False, True, True])

    def test_active_subscription_is_unlimited(self):
        user = User.objects.create_user("pro@example.com", email="pro@example.com")
        Subscription.objects.create(
            user=user, paypal_subscription_id="I-PRO",
            status=Subscription.STATUS_ACTIVE,
            current_period_end=timezone.now() + timedelta(days=30),
        )
        user.refresh_from_db()
        request = self._request(user=user)
        results = [check_access(request, p.pk)["locked"] for p in self.props]
        self.assertEqual(results, [False] * 6)
        self.assertEqual(PropertyView.objects.filter(user=user).count(), 0,
                         "unlimited tier shouldn't record views it never counts")

    def test_expired_subscription_falls_back_to_free_limit(self):
        user = User.objects.create_user("ex@example.com", email="ex@example.com")
        Subscription.objects.create(
            user=user, paypal_subscription_id="I-EX",
            status=Subscription.STATUS_EXPIRED,
            current_period_end=timezone.now() - timedelta(days=1),
        )
        user.refresh_from_db()
        request = self._request(user=user)
        results = [check_access(request, p.pk)["locked"] for p in self.props[:5]]
        self.assertEqual(results, [False, False, False, True, True])
