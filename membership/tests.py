"""Tests for the parts of billing and metering that decide who gets access.

The webhook can't be exercised end to end locally — PayPal only delivers to a
public https:// URL — so signature verification is mocked and the state machine
underneath is tested directly. That machine is what actually grants and revokes
Pro, so it's the part worth covering: a wrong transition either gives away
access for free or locks out someone who is paying.
"""

import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from inventory.models import Property
from membership.metering import check_access
from membership.models import (
    Consultation,
    ProAttempt,
    PropertyView,
    Subscription,
)

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
class WalledPageTests(TestCase):
    """What the walled page actually renders.

    check_access can be perfectly correct while the page still shows everything,
    because the withholding happens in the template. That is exactly how the
    areas stayed visible past the limit, and how the wall came to advertise
    "unlock the land area" directly beneath a visible land area — so these
    assertions are on the response body, not on the access dict.
    """

    def setUp(self):
        self.props = [
            Property.objects.create(
                url=f"http://x/w{i}", price=1000, show_in_front=True,
                location="Oita City, Oita Prefecture",
                building_area="103㎡", land_area="101㎡ (public book)",
            )
            for i in range(4)
        ]
        self.client = Client(HTTP_USER_AGENT="Mozilla/5.0")

    def get(self, prop):
        return self.client.get(f"/japanese-houses/{prop.pk}/0/")

    def test_areas_show_inside_the_allowance(self):
        body = self.get(self.props[0]).content.decode()
        self.assertIn("103㎡", body)
        self.assertIn("101㎡ (public book)", body)

    def test_areas_are_withheld_once_the_allowance_is_spent(self):
        for p in self.props[:2]:          # spend the 2-view anonymous allowance
            self.get(p)
        body = self.get(self.props[2]).content.decode()
        self.assertNotIn("103㎡", body, "building area must be withheld past the limit")
        self.assertNotIn("101㎡ (public book)", body, "land area too")
        self.assertIn("Create a free account", body)

    def test_the_wall_persists_on_every_further_property(self):
        """The reported symptom was the wall vanishing on the next click."""
        for p in self.props[:2]:
            self.get(p)
        for p in self.props[2:]:
            body = self.get(p).content.decode()
            self.assertNotIn("103㎡", body, f"pk={p.pk} leaked the area")
            self.assertIn("Create a free account", body)

    def test_revisiting_an_allowed_property_still_shows_its_areas(self):
        """Spending the allowance must not retroactively strip the properties
        already opened."""
        for p in self.props[:2]:
            self.get(p)
        self.get(self.props[2])           # hit the wall
        body = self.get(self.props[0]).content.decode()
        self.assertIn("103㎡", body)

    def test_crawler_sees_the_areas_past_the_limit(self):
        crawler = Client(HTTP_USER_AGENT="Mozilla/5.0 (compatible; Googlebot/2.1)")
        for p in self.props:
            body = crawler.get(f"/japanese-houses/{p.pk}/0/").content.decode()
            self.assertIn("103㎡", body, "Googlebot must never be walled")


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


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    PAYPAL_CLIENT_ID="id", PAYPAL_CLIENT_SECRET="secret",
    CONSULT_TIMEZONE="Asia/Tokyo", CONSULT_WEEKDAYS=[0, 1, 2, 3, 4],
    CONSULT_OPEN="10:00", CONSULT_CLOSE="18:00",
    CONSULT_DURATION_MINUTES=30, CONSULT_SLOT_STEP_MINUTES=30,
    CONSULT_LEAD_HOURS=24, CONSULT_HORIZON_DAYS=14, CONSULT_HOLD_MINUTES=20,
    CONSULT_PRICE="25.00", CONSULT_CURRENCY="USD",
)
class ConsultationBookingTests(TestCase):
    """The booking flow, where a mistake either sells a slot twice or gives a
    call away free.

    PayPal is mocked throughout: create_order and capture_order are the only two
    calls that leave the process, and exercising them for real would mean taking
    money in a test.
    """

    def setUp(self):
        from membership.scheduling import available_slots
        self.client = Client(HTTP_USER_AGENT="Mozilla/5.0")
        self.slot = available_slots()[0]
        self.listing = Property.objects.create(
            url="http://x/consult", price=1200, show_in_front=True,
            location="Oita City, Oita Prefecture",
        )

    def _post(self, **overrides):
        data = {
            "starts_at": self.slot.isoformat(),
            "name": "Ada Lovelace",
            "email": "ada@example.com",
            "notes": "Thinking about Nagano.",
            "timezone": "Europe/Madrid",
        }
        data.update(overrides)
        return self.client.post("/consultation/book", data)

    # -- holding the slot -------------------------------------------------

    def test_booking_creates_a_hold_and_returns_paypal(self):
        with patch("membership.consultations.create_order",
                   return_value=("ORDER-1", "https://paypal.test/approve")) as create:
            response = self._post(listing=str(self.listing.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["redirect"], "https://paypal.test/approve")

        booking = Consultation.objects.get()
        self.assertEqual(booking.status, Consultation.STATUS_HOLD)
        self.assertEqual(booking.paypal_order_id, "ORDER-1")
        self.assertEqual(booking.listing, self.listing)
        self.assertEqual(booking.visitor_timezone, "Europe/Madrid")
        self.assertIsNotNone(booking.hold_expires_at)
        # The price charged comes from settings, never from the request.
        self.assertEqual(str(booking.amount), "25.00")
        self.assertEqual(create.call_args.kwargs["amount"], Decimal("25.00"))

    def test_price_cannot_be_set_from_the_request(self):
        with patch("membership.consultations.create_order",
                   return_value=("ORDER-2", "https://paypal.test/a")) as create:
            self._post(amount="0.01", price="0.01", currency="XXX")
        self.assertEqual(create.call_args.kwargs["amount"], Decimal("25.00"))
        self.assertEqual(create.call_args.kwargs["currency"], "USD")

    def test_a_held_slot_is_no_longer_offered(self):
        from membership.scheduling import available_slots
        with patch("membership.consultations.create_order",
                   return_value=("ORDER-3", "https://paypal.test/a")):
            self._post()
        self.assertNotIn(self.slot, available_slots())

    def test_second_booking_of_the_same_slot_is_refused(self):
        with patch("membership.consultations.create_order",
                   return_value=("ORDER-4", "https://paypal.test/a")):
            self._post()
        with patch("membership.consultations.create_order",
                   return_value=("ORDER-5", "https://paypal.test/a")):
            response = self._post(email="eve@example.com")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Consultation.objects.count(), 1)

    def test_concurrent_booking_is_stopped_by_the_constraint(self):
        """Both requests pass the availability check, so only the unique
        constraint can stop the second one."""
        with patch("membership.consultations.create_order",
                   return_value=("ORDER-6", "https://paypal.test/a")):
            self._post()
        with patch("membership.consultations.is_available", return_value=True), \
             patch("membership.consultations.create_order",
                   return_value=("ORDER-7", "https://paypal.test/a")):
            response = self._post(email="mallory@example.com")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Consultation.objects.count(), 1)

    def test_expired_hold_frees_the_slot(self):
        from membership.scheduling import available_slots
        with patch("membership.consultations.create_order",
                   return_value=("ORDER-8", "https://paypal.test/a")):
            self._post()
        booking = Consultation.objects.get()
        booking.hold_expires_at = timezone.now() - timedelta(minutes=1)
        booking.save(update_fields=["hold_expires_at"])
        self.assertIn(self.slot, available_slots())

    def test_paypal_failure_releases_the_slot_immediately(self):
        from membership.paypal_orders import PayPalError
        from membership.scheduling import available_slots
        with patch("membership.consultations.create_order",
                   side_effect=PayPalError("nope")):
            response = self._post()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            Consultation.objects.get().status, Consultation.STATUS_CANCELLED
        )
        self.assertIn(self.slot, available_slots())

    def test_unavailable_slot_is_refused(self):
        past = timezone.now() - timedelta(days=1)
        response = self._post(starts_at=past.isoformat())
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Consultation.objects.exists())

    def test_missing_contact_details_are_refused(self):
        self.assertEqual(self._post(name="").status_code, 400)
        self.assertEqual(self._post(email="").status_code, 400)
        self.assertFalse(Consultation.objects.exists())

    def test_a_bogus_timezone_falls_back_rather_than_being_stored(self):
        with patch("membership.consultations.create_order",
                   return_value=("ORDER-9", "https://paypal.test/a")):
            self._post(timezone="Mars/Olympus_Mons")
        self.assertEqual(Consultation.objects.get().visitor_timezone, "UTC")

    # -- taking the money -------------------------------------------------

    def _hold(self, order_id="ORDER-CAP"):
        return Consultation.objects.create(
            starts_at=self.slot, duration_minutes=30, name="Ada",
            email="ada@example.com", visitor_timezone="Europe/Madrid",
            status=Consultation.STATUS_HOLD, hold_expires_at=timezone.now() + timedelta(minutes=20),
            paypal_order_id=order_id, amount=Decimal("25.00"), currency="USD",
        )

    def _captured(self, paid=True, **over):
        data = {
            "order_id": "ORDER-CAP", "status": "COMPLETED", "capture_id": "CAP-1",
            "capture_status": "COMPLETED" if paid else "PENDING",
            "amount": "25.00", "currency": "USD", "reference": "1",
            "payer_email": "ada@example.com", "paid": paid,
        }
        data.update(over)
        return data

    def test_capture_marks_it_paid_and_emails(self):
        booking = self._hold()
        with patch("membership.consultations.capture_order", return_value=self._captured()), \
             patch("membership.consultation_mail.send_confirmation") as mail:
            response = self.client.get("/consultation/booked?token=ORDER-CAP")
        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Consultation.STATUS_PAID)
        self.assertEqual(booking.paypal_capture_id, "CAP-1")
        self.assertIsNotNone(booking.paid_at)
        self.assertIsNone(booking.hold_expires_at)
        mail.assert_called_once()

    def test_uncaptured_payment_is_not_a_booking(self):
        """Approved-but-not-captured is a payer who stopped at the button."""
        booking = self._hold()
        with patch("membership.consultations.capture_order",
                   return_value=self._captured(paid=False)):
            response = self.client.get("/consultation/booked?token=ORDER-CAP")
        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Consultation.STATUS_HOLD)

    def test_reloading_the_receipt_does_not_capture_twice(self):
        booking = self._hold()
        with patch("membership.consultations.capture_order", return_value=self._captured()), \
             patch("membership.consultation_mail.send_confirmation"):
            self.client.get("/consultation/booked?token=ORDER-CAP")
        with patch("membership.consultations.capture_order") as capture:
            self.client.get("/consultation/booked?token=ORDER-CAP")
        capture.assert_not_called()

    def test_email_failure_does_not_lose_a_paid_booking(self):
        booking = self._hold()
        with patch("membership.consultations.capture_order", return_value=self._captured()), \
             patch("membership.consultation_mail.send_confirmation",
                   side_effect=Exception("smtp down")):
            response = self.client.get("/consultation/booked?token=ORDER-CAP")
        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Consultation.STATUS_PAID)

    def test_capture_failure_leaves_it_unpaid(self):
        from membership.paypal_orders import PayPalError
        booking = self._hold()
        with patch("membership.consultations.capture_order",
                   side_effect=PayPalError("declined")):
            response = self.client.get("/consultation/booked?token=ORDER-CAP")
        self.assertEqual(response.status_code, 502)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Consultation.STATUS_HOLD)

    def test_unknown_token_is_a_404_not_a_crash(self):
        response = self.client.get("/consultation/booked?token=NOPE")
        self.assertEqual(response.status_code, 404)

    def test_cancel_url_releases_the_hold(self):
        from membership.scheduling import available_slots
        self._hold(order_id="ORDER-X")
        response = self.client.get("/consultation/cancelled?token=ORDER-X")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Consultation.objects.get().status, Consultation.STATUS_CANCELLED
        )
        self.assertIn(self.slot, available_slots())

    # -- the calendar invitation -----------------------------------------

    def test_ics_is_well_formed_and_in_utc(self):
        from membership.consultation_mail import build_ics
        booking = self._hold()
        booking.status = Consultation.STATUS_PAID
        booking.save()
        ics = build_ics(booking)
        self.assertTrue(ics.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertIn("\r\n", ics, "RFC 5545 requires CRLF")
        self.assertIn(f"UID:consultation-{booking.pk}@akiyainjapan.com", ics)
        self.assertIn(booking.starts_at.strftime("DTSTART:%Y%m%dT%H%M%SZ"), ics)
        self.assertIn(booking.ends_at.strftime("DTEND:%Y%m%dT%H%M%SZ"), ics)
        self.assertIn("END:VCALENDAR", ics)

    def test_confirmation_states_both_timezones(self):
        from django.core import mail as django_mail
        from membership.consultation_mail import send_confirmation
        booking = self._hold()
        booking.status = Consultation.STATUS_PAID
        booking.save()
        send_confirmation(booking)
        self.assertEqual(len(django_mail.outbox), 2, "payer and owner")
        payer = django_mail.outbox[0]
        self.assertIn("Europe/Madrid", payer.body)
        self.assertIn("Asia/Tokyo", payer.body)
        self.assertEqual(payer.attachments[0][0], "consultation.ics")


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    CONSULT_TIMEZONE="Asia/Tokyo", CONSULT_WEEKDAYS=[0, 1, 2, 3, 4],
    CONSULT_OPEN="10:00", CONSULT_CLOSE="18:00",
    CONSULT_DURATION_MINUTES=60, CONSULT_SLOT_STEP_MINUTES=30,
    CONSULT_LEAD_HOURS=24, CONSULT_HORIZON_DAYS=14,
)
class SchedulingTests(TestCase):
    """Slot generation. Timezones and the window edges are where this goes wrong
    quietly — an off-by-one here books the agent at 3am."""

    def test_every_slot_is_inside_the_window_in_the_agents_zone(self):
        from zoneinfo import ZoneInfo
        from membership.scheduling import available_slots
        for slot in available_slots():
            local = slot.astimezone(ZoneInfo("Asia/Tokyo"))
            self.assertGreaterEqual(local.hour, 10)
            # A 60-minute call must finish by 18:00, so the last start is 17:00.
            self.assertLessEqual(local.hour, 17)
            if local.hour == 17:
                self.assertEqual(local.minute, 0)

    def test_no_slots_on_excluded_weekdays(self):
        from zoneinfo import ZoneInfo
        from membership.scheduling import available_slots
        for slot in available_slots():
            self.assertLess(slot.astimezone(ZoneInfo("Asia/Tokyo")).weekday(), 5)

    def test_lead_time_and_horizon_are_respected(self):
        from membership.scheduling import available_slots
        now = timezone.now()
        slots = available_slots(now=now)
        self.assertTrue(all(s >= now + timedelta(hours=24) for s in slots))
        self.assertTrue(all(s <= now + timedelta(days=14) for s in slots))

    def test_grouping_uses_the_viewers_day_not_ours(self):
        """A 10:00 Tokyo slot is the previous evening in New York, and must be
        filed under the date the viewer will actually read."""
        from zoneinfo import ZoneInfo
        from membership.scheduling import available_slots, group_by_day
        slots = available_slots()
        for day, day_slots in group_by_day(slots, "America/New_York"):
            for slot in day_slots:
                self.assertEqual(slot.astimezone(ZoneInfo("America/New_York")).date(), day)

    def test_a_bogus_display_zone_falls_back_to_utc(self):
        from membership.scheduling import available_slots, group_by_day
        self.assertTrue(group_by_day(available_slots(), "Nowhere/Fake"))


@override_settings(ALLOWED_HOSTS=["testserver"])
class SubscriptionAttemptTests(TestCase):
    """Recording that somebody *started* Pro.

    The point of this endpoint is funnel visibility, so the risks are the two
    ways it could do harm: handing out access without payment, or downgrading a
    paying member's row.
    """

    def setUp(self):
        self.user = User.objects.create_user("try@example.com", email="try@example.com")
        self.client = Client(HTTP_USER_AGENT="Mozilla/5.0")

    def post(self):
        return self.client.post("/api/pro-checkout-started", "{}",
                                content_type="application/json")

    def test_anonymous_is_refused(self):
        self.assertEqual(self.post().status_code, 401)
        self.assertFalse(Subscription.objects.exists())

    def test_attempt_is_recorded_without_granting_access(self):
        self.client.force_login(self.user)
        response = self.post()
        self.assertEqual(response.status_code, 200)
        sub = Subscription.objects.get()
        self.assertEqual(sub.status, Subscription.STATUS_APPROVAL_PENDING)
        self.assertFalse(sub.is_active, "an attempt must not grant Pro")
        self.user.refresh_from_db()
        from membership.metering import user_is_pro
        self.assertFalse(user_is_pro(self.user))

    def test_two_different_users_can_both_attempt(self):
        """paypal_subscription_id is unique, so attempts must not collide —
        storing "" for both would raise IntegrityError on the second."""
        other = User.objects.create_user("two@example.com", email="two@example.com")
        self.client.force_login(self.user)
        self.assertEqual(self.post().status_code, 200)
        self.client.force_login(other)
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(Subscription.objects.count(), 2)

    def test_repeat_attempt_does_not_duplicate(self):
        self.client.force_login(self.user)
        self.post()
        self.post()
        self.assertEqual(Subscription.objects.count(), 1)

    def test_an_active_member_is_not_downgraded(self):
        """A Pro member idly clicking the button again must keep their access."""
        Subscription.objects.create(
            user=self.user, paypal_subscription_id="I-REAL",
            status=Subscription.STATUS_ACTIVE,
            current_period_end=timezone.now() + timedelta(days=20),
        )
        self.client.force_login(self.user)
        self.post()
        sub = Subscription.objects.get()
        self.assertEqual(sub.status, Subscription.STATUS_ACTIVE)
        self.assertEqual(sub.paypal_subscription_id, "I-REAL")
        self.assertTrue(sub.is_active)

    def test_approval_upgrades_the_attempt_row(self):
        """The attempt and the real subscription are the same row, so the funnel
        shows one person rather than two."""
        self.client.force_login(self.user)
        self.post()
        self.client.post(
            "/api/register-subscription",
            json.dumps({"subscription_id": "I-APPROVED"}),
            content_type="application/json",
        )
        sub = Subscription.objects.get()
        self.assertEqual(sub.status, Subscription.STATUS_ACTIVE)
        self.assertEqual(sub.paypal_subscription_id, "I-APPROVED")


@override_settings(ALLOWED_HOSTS=["testserver"])
class ProAttemptLogTests(TestCase):
    """The log of people trying to pay for Pro.

    Two things matter. It has to catch the attempts Subscription cannot
    represent — the ones made while there is no PayPal plan to subscribe to,
    which used to disappear entirely — and it must stay a log: no row here may
    ever imply access.
    """

    def setUp(self):
        self.user = User.objects.create_user("want@example.com",
                                             email="want@example.com")
        self.client = Client(HTTP_USER_AGENT="Mozilla/5.0")

    def want(self, body="{}"):
        return self.client.post("/api/pro-wanted", body,
                                content_type="application/json")

    def test_anonymous_is_refused(self):
        self.assertEqual(self.want().status_code, 401)
        self.assertFalse(ProAttempt.objects.exists())

    @override_settings(PAYPAL_CLIENT_ID="", PAYPAL_PLAN_ID="")
    def test_wanting_pro_while_it_is_unbuyable_is_logged(self):
        """The whole reason this exists: nothing to buy, and we still know."""
        self.client.force_login(self.user)
        self.assertEqual(self.want().status_code, 200)

        attempt = ProAttempt.objects.get()
        self.assertEqual(attempt.source, ProAttempt.SOURCE_WAITLIST)
        self.assertEqual(attempt.email, "want@example.com")
        self.assertFalse(attempt.billing_configured)
        # No entitlement anywhere: not in Subscription, not in the metering.
        self.assertFalse(Subscription.objects.exists())
        from membership.metering import user_is_pro
        self.assertFalse(user_is_pro(self.user))

    @override_settings(PAYPAL_CLIENT_ID="", PAYPAL_PLAN_ID="")
    def test_repeat_clicks_are_separate_rows(self):
        """A log, not a state machine — somebody asking twice is two data points."""
        self.client.force_login(self.user)
        self.want()
        self.want()
        self.assertEqual(ProAttempt.objects.count(), 2)

    @override_settings(PAYPAL_CLIENT_ID="", PAYPAL_PLAN_ID="")
    def test_the_originating_page_is_kept(self):
        self.client.force_login(self.user)
        self.want(json.dumps({"from": "/japanese-houses/12/"}))
        self.assertEqual(ProAttempt.objects.get().from_url, "/japanese-houses/12/")

    @override_settings(PAYPAL_CLIENT_ID="AAA", PAYPAL_PLAN_ID="P-1")
    def test_checkout_attempts_are_logged_too(self):
        """One table covers both halves of /pro/, so the funnel is one number."""
        self.client.force_login(self.user)
        self.client.post("/api/pro-checkout-started", "{}",
                         content_type="application/json")

        attempt = ProAttempt.objects.get()
        self.assertEqual(attempt.source, ProAttempt.SOURCE_CHECKOUT)
        self.assertTrue(attempt.billing_configured,
                        "a plan was configured, so this person could actually pay")

    @override_settings(PAYPAL_CLIENT_ID="AAA", PAYPAL_PLAN_ID="P-1")
    def test_an_active_member_clicking_again_is_logged_and_keeps_access(self):
        Subscription.objects.create(
            user=self.user, paypal_subscription_id="I-REAL",
            status=Subscription.STATUS_ACTIVE,
            current_period_end=timezone.now() + timedelta(days=20),
        )
        self.client.force_login(self.user)
        self.client.post("/api/pro-checkout-started", "{}",
                         content_type="application/json")
        self.assertEqual(ProAttempt.objects.count(), 1)
        self.assertTrue(Subscription.objects.get().is_active)

    @override_settings(PAYPAL_CLIENT_ID="", PAYPAL_PLAN_ID="")
    def test_a_junk_email_cookie_does_not_break_the_insert(self):
        """The `email` cookie holds whatever an older form was given."""
        self.client.force_login(self.user)
        self.user.email = ""
        self.user.save(update_fields=["email"])
        self.client.cookies["email"] = "not-an-email"
        self.assertEqual(self.want().status_code, 200)
        self.assertEqual(ProAttempt.objects.get().email, "")

    @override_settings(PAYPAL_CLIENT_ID="", PAYPAL_PLAN_ID="")
    def test_a_deleted_account_leaves_the_attempt_behind(self):
        """Who wanted Pro survives them deleting the account, via the flat email."""
        self.client.force_login(self.user)
        self.want()
        self.user.delete()
        attempt = ProAttempt.objects.get()
        self.assertIsNone(attempt.user)
        self.assertEqual(attempt.email, "want@example.com")

    @override_settings(PAYPAL_CLIENT_ID="", PAYPAL_PLAN_ID="")
    def test_the_admin_list_renders_with_its_summary(self):
        """The changelist has a custom template and two computed columns, so a
        typo in either only shows up when somebody opens the page."""
        self.client.force_login(self.user)
        self.want()
        staff = User.objects.create_superuser("boss@example.com", "boss@example.com",
                                              "x")
        self.client.force_login(staff)
        page = self.client.get("/admin/membership/proattempt/")
        self.assertEqual(page.status_code, 200)
        body = page.content.decode()
        self.assertIn("Attempts to pay for Pro", body)
        self.assertIn("Wanted it while it was unbuyable", body)
        self.assertIn("want@example.com", body)

    @override_settings(PAYPAL_CLIENT_ID="", PAYPAL_PLAN_ID="")
    def test_the_waitlist_page_offers_the_button(self):
        """With billing off, /pro/ must show the recorded CTA, not a dead link."""
        self.client.force_login(self.user)
        page = self.client.get("/pro/").content.decode()
        self.assertIn('id="pro-wanted"', page)
        self.assertIn("/api/pro-wanted", page)


@override_settings(ALLOWED_HOSTS=["testserver"])
class InspectionRequestTests(TestCase):
    """The inspection lead capture.

    It takes no money, so the risks are different from the paid flows: losing a
    high-intent lead, or recording one we cannot reply to.
    """

    URL = "/api/request-inspection"

    def setUp(self):
        self.prop = Property.objects.create(
            url="http://x/insp", price=1500, show_in_front=True,
            location="Nagano City, Nagano Prefecture",
        )
        self.client = Client(HTTP_USER_AGENT="Mozilla/5.0")

    def post(self, **body):
        return self.client.post(self.URL, json.dumps(body),
                                content_type="application/json")

    def test_anonymous_with_an_email_is_accepted(self):
        """The highest-intent signal on the site; an account requirement here
        would cost more leads than the spam it prevents."""
        r = self.post(email="buyer@example.com", property_id=self.prop.pk,
                      notes="Worried about the roof.")
        self.assertEqual(r.status_code, 200)
        from membership.models import InspectionRequest
        req = InspectionRequest.objects.get()
        self.assertEqual(req.email, "buyer@example.com")
        self.assertEqual(req.listing, self.prop)
        self.assertEqual(req.status, InspectionRequest.STATUS_NEW)
        self.assertTrue(req.needs_reply)
        self.assertIn("roof", req.notes)

    def test_the_property_is_recorded_flat_as_well_as_by_link(self):
        """Listings get delisted and the FK goes null; without these the record
        no longer says what they asked about."""
        self.post(email="b@example.com", property_id=self.prop.pk)
        from membership.models import InspectionRequest
        req = InspectionRequest.objects.get()
        self.assertIn("Nagano", req.listing_location)
        self.assertIn(str(self.prop.pk), req.listing_url)
        self.prop.delete()
        req.refresh_from_db()
        self.assertIsNone(req.listing, "FK is cleared")
        self.assertIn("Nagano", req.listing_location, "but we still know where")

    def test_signed_in_user_needs_no_email_field(self):
        user = User.objects.create_user("insp@example.com", email="insp@example.com")
        self.client.force_login(user)
        r = self.post(property_id=self.prop.pk)
        self.assertEqual(r.status_code, 200)
        from membership.models import InspectionRequest
        req = InspectionRequest.objects.get()
        self.assertEqual(req.email, "insp@example.com")
        self.assertEqual(req.user, user)

    def test_a_bad_email_is_refused_with_a_usable_message(self):
        r = self.post(email="not-an-email", property_id=self.prop.pk)
        self.assertEqual(r.status_code, 400)
        self.assertIn("email", r.json()["error"].lower())
        from membership.models import InspectionRequest
        self.assertFalse(InspectionRequest.objects.exists())

    def test_no_email_at_all_is_refused(self):
        self.assertEqual(self.post(property_id=self.prop.pk).status_code, 400)

    def test_malformed_json_does_not_500(self):
        r = self.client.post(self.URL, "not json", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_it_notifies_someone(self):
        from django.core import mail as django_mail
        self.post(email="buyer@example.com", property_id=self.prop.pk,
                  notes="Timeline is spring.")
        self.assertEqual(len(django_mail.outbox), 1)
        body = django_mail.outbox[0].body
        self.assertIn("buyer@example.com", body)
        self.assertIn("Nagano", body)
        self.assertIn("spring", body)
        self.assertIn("Nothing has been charged", body)

    def test_a_failed_notification_does_not_lose_the_lead(self):
        with patch("membership.utils.notify_inspection_request",
                   side_effect=Exception("smtp down")):
            r = self.post(email="buyer@example.com", property_id=self.prop.pk)
        self.assertEqual(r.status_code, 200)
        from membership.models import InspectionRequest
        self.assertEqual(InspectionRequest.objects.count(), 1)

    def test_it_charges_nothing(self):
        """The whole point: no Consultation, no Subscription, no PayPal."""
        self.post(email="buyer@example.com", property_id=self.prop.pk)
        self.assertFalse(Consultation.objects.exists())
        self.assertFalse(Subscription.objects.exists())

    def test_a_request_without_a_property_still_records(self):
        r = self.post(email="buyer@example.com")
        self.assertEqual(r.status_code, 200)
        from membership.models import InspectionRequest
        self.assertEqual(InspectionRequest.objects.get().listing, None)


@override_settings(ALLOWED_HOSTS=["testserver"])
class SignupTests(TestCase):
    """Signing up with an email and a password, more than once.

    This exists because of a specific outage: settings carried
    ACCOUNT_USER_MODEL_USERNAME_FIELD = None, which tells allauth the user model
    has no username field. We use Django's default User, whose `username` is
    present, unique and non-nullable, so allauth left it empty — the first signup
    stored '' and every one after it died with "UNIQUE constraint failed:
    auth_user.username". One signup passes on a fresh database, which is why it
    survived to production; the second is the test that matters.
    """

    URL = "/accounts/signup/"
    PASSWORD = "sturdy-passphrase-42"

    def signup(self, email):
        return self.client.post(
            self.URL,
            {"email": email, "password1": self.PASSWORD, "password2": self.PASSWORD},
        )

    def test_a_second_signup_does_not_collide(self):
        self.assertEqual(self.signup("first@example.com").status_code, 302)
        self.client = Client()
        self.assertEqual(self.signup("second@example.com").status_code, 302)
        self.assertEqual(User.objects.count(), 2)

    def test_every_user_gets_a_non_empty_unique_username(self):
        for email in ("a@example.com", "b@example.com", "a@other.com"):
            self.client = Client()
            self.signup(email)
        names = list(User.objects.values_list("username", flat=True))
        self.assertNotIn("", names)
        self.assertEqual(len(names), len(set(names)), f"duplicates in {names}")

    def test_nobody_is_asked_for_a_username(self):
        """The username is derived, never requested — the signup form has one
        field for identity and it is the email."""
        self.assertNotIn('name="username"', self.client.get(self.URL).content.decode())

    def test_the_new_account_can_log_in_by_email(self):
        self.signup("comeback@example.com")
        c = Client()
        c.post("/accounts/login/", {"login": "comeback@example.com",
                                    "password": self.PASSWORD})
        self.assertIn("_auth_user_id", c.session)


@override_settings(ALLOWED_HOSTS=["testserver"], DESK_REPORT_SAMPLE_PK=0,
                   DESK_REPORT_PRO_ALLOWANCE=3, DESK_REPORT_WINDOW_DAYS=30)
class DeskReportAllowanceTests(TestCase):
    """Who may claim a report, and when.

    Three a month on a rolling 30-day window. The tests that matter are the
    refusals: that the third claim still works, that the fourth does not, that
    the window really rolls, and that being out this month reads as "the next one
    is in N days" rather than as a flat no.
    """

    def setUp(self):
        self.user = User.objects.create_user("pro@example.com",
                                             email="pro@example.com")
        self.listing = Property.objects.create(
            url="https://example.com/allowance", title="A house", price=1200,
            floor_plan="3LDK", location="Oita Prefecture",
        )

    def make_pro(self, user=None):
        Subscription.objects.create(
            user=user or self.user, paypal_subscription_id=f"I-{(user or self.user).pk}",
            status=Subscription.STATUS_ACTIVE,
            current_period_end=timezone.now() + timedelta(days=20),
        )

    def claim(self, when=None, status=None, listing=None):
        from membership.models import DeskReportRequest

        request = DeskReportRequest.objects.create(
            user=self.user, email=self.user.email,
            listing=listing if listing is not None else self.listing,
            status=status or DeskReportRequest.STATUS_REQUESTED,
        )
        if when:
            DeskReportRequest.objects.filter(pk=request.pk).update(created_at=when)
            request.refresh_from_db()
        return request

    def allowance(self):
        from membership.desk_report_allowance import allowance_for

        return allowance_for(self.user)

    def test_a_free_account_has_no_allowance(self):
        allowance = self.allowance()
        self.assertFalse(allowance["is_pro"])
        self.assertFalse(allowance["can_claim"])
        self.assertEqual(allowance["blocked_by"], "not_pro")

    def test_an_anonymous_visitor_has_no_allowance(self):
        from django.contrib.auth.models import AnonymousUser
        from membership.desk_report_allowance import allowance_for

        self.assertEqual(allowance_for(AnonymousUser())["blocked_by"], "not_pro")

    def test_a_new_pro_member_can_claim_immediately(self):
        self.make_pro()
        allowance = self.allowance()
        self.assertTrue(allowance["can_claim"])
        self.assertEqual(allowance["remaining"], 3)

    def test_all_three_can_be_claimed_in_the_same_week(self):
        """Three a month means three, not one paced across the month."""
        self.make_pro()
        for _ in range(2):
            self.claim()
        allowance = self.allowance()
        self.assertTrue(allowance["can_claim"])
        self.assertEqual(allowance["remaining"], 1)

    def test_the_fourth_claim_in_a_month_is_refused_with_a_date(self):
        self.make_pro()
        self.claim(when=timezone.now() - timedelta(days=20))
        self.claim(when=timezone.now() - timedelta(days=10))
        self.claim()
        allowance = self.allowance()
        self.assertFalse(allowance["can_claim"])
        self.assertEqual(allowance["blocked_by"], "exhausted")
        self.assertEqual(allowance["used"], 3)
        # The window rolls off the oldest claim, 20 days ago, so ~10 days.
        self.assertTrue(8 <= allowance["days_until_next"] <= 12,
                        allowance["days_until_next"])

    def test_the_allowance_renews_as_the_window_rolls(self):
        """Claims older than the window stop counting — this is the difference
        between three a month and three ever."""
        self.make_pro()
        for _ in range(3):
            self.claim(when=timezone.now() - timedelta(days=31))
        allowance = self.allowance()
        self.assertEqual(allowance["used"], 0)
        self.assertEqual(allowance["remaining"], 3)
        self.assertTrue(allowance["can_claim"])

    def test_a_declined_request_gives_the_allowance_back(self):
        """A listing we could not work on must not cost the member a report."""
        from membership.models import DeskReportRequest

        self.make_pro()
        for _ in range(3):
            self.claim()
        self.assertEqual(self.allowance()["remaining"], 0)

        newest = DeskReportRequest.objects.order_by("-created_at").first()
        newest.status = DeskReportRequest.STATUS_DECLINED
        newest.save()
        self.assertEqual(self.allowance()["remaining"], 1)

    def test_a_delivered_report_still_counts_inside_the_window(self):
        from membership.models import DeskReportRequest

        self.make_pro()
        self.claim(when=timezone.now() - timedelta(days=5),
                   status=DeskReportRequest.STATUS_DELIVERED)
        self.assertEqual(self.allowance()["used"], 1)

    def test_asking_twice_about_the_same_house_is_refused_without_cost(self):
        from membership.desk_report_allowance import claim_error

        self.make_pro()
        self.claim()
        error = claim_error(self.user, self.listing)
        self.assertIn("already asked", error)
        self.assertEqual(self.allowance()["remaining"], 2)

    def test_staff_are_pro_for_this_too(self):
        """user_is_pro treats staff as Pro; the allowance must agree rather than
        quietly diverge from the rest of the metering."""
        staff = User.objects.create_user("staff@example.com",
                                         email="staff@example.com", is_staff=True)
        from membership.desk_report_allowance import allowance_for

        self.assertTrue(allowance_for(staff)["is_pro"])


@override_settings(ALLOWED_HOSTS=["testserver"], DESK_REPORT_SAMPLE_PK=0,
                   DESK_REPORT_PRO_ALLOWANCE=3, DESK_REPORT_COOLDOWN_DAYS=30)
class DeskReportClaimTests(TestCase):
    """The endpoint the property page posts to."""

    def setUp(self):
        self.client = Client(HTTP_USER_AGENT="Mozilla/5.0")
        self.user = User.objects.create_user("pro@example.com",
                                             email="pro@example.com")
        self.listing = Property.objects.create(
            url="https://example.com/claim", title="A house in Oita", price=1200,
            floor_plan="3LDK", location="Oita Prefecture",
        )

    def make_pro(self):
        Subscription.objects.create(
            user=self.user, paypal_subscription_id="I-CLAIM",
            status=Subscription.STATUS_ACTIVE,
            current_period_end=timezone.now() + timedelta(days=20),
        )

    def post(self, **fields):
        payload = {"listing": self.listing.pk}
        payload.update(fields)
        return self.client.post("/api/request-desk-report", payload)

    def test_anonymous_is_refused(self):
        self.assertEqual(self.post().status_code, 401)

    def test_a_free_account_is_told_it_is_a_pro_feature(self):
        self.client.force_login(self.user)
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertIn("Pro", response.json()["error"])

    def test_a_pro_member_can_claim_and_gets_the_count_back(self):
        from membership.models import DeskReportRequest

        self.make_pro()
        self.client.force_login(self.user)
        response = self.post(notes="Could I run it as a guesthouse?")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["remaining"], 2)
        claim = DeskReportRequest.objects.get()
        self.assertEqual(claim.user, self.user)
        self.assertEqual(claim.listing, self.listing)
        self.assertEqual(claim.buyer_notes, "Could I run it as a guesthouse?")
        self.assertTrue(claim.is_owed)

    def test_the_allowance_is_rechecked_at_claim_time(self):
        """The button's state was decided when the page was rendered, and two
        tabs can both show it enabled."""
        from membership.models import DeskReportRequest

        self.make_pro()
        self.client.force_login(self.user)
        listings = [self.listing] + [
            Property.objects.create(
                url=f"https://example.com/claim{n}", title=f"House {n}", price=900,
                floor_plan="2DK", location="Oita Prefecture",
            )
            for n in range(2, 5)
        ]
        codes = [
            self.client.post("/api/request-desk-report",
                             {"listing": listing.pk}).status_code
            for listing in listings
        ]
        self.assertEqual(codes, [200, 200, 200, 409],
                         "three a month, and the fourth refused server-side")
        self.assertEqual(DeskReportRequest.objects.count(), 3)

    def test_claiming_emails_the_member_and_us(self):
        from django.core import mail

        self.make_pro()
        self.client.force_login(self.user)
        self.post()
        self.assertEqual(len(mail.outbox), 2)

    def test_an_email_failure_does_not_lose_the_claim(self):
        from membership.models import DeskReportRequest

        self.make_pro()
        self.client.force_login(self.user)
        with patch("membership.desk_reports._notify",
                   side_effect=RuntimeError("smtp down")):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(DeskReportRequest.objects.exists())

    def test_a_claim_needs_a_property(self):
        self.make_pro()
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.post("/api/request-desk-report", {}).status_code, 400
        )


@override_settings(ALLOWED_HOSTS=["testserver"], DESK_REPORT_SAMPLE_PK=0,
                   DESK_REPORT_PRO_ALLOWANCE=3)
class DeskReportPreviewOnPropertyPageTests(TestCase):
    """The teaser. It shows what the report really found on this listing and
    withholds the reasoning — so the test that matters is that the reasoning is
    genuinely absent, not just visually hidden."""

    def setUp(self):
        self.client = Client(HTTP_USER_AGENT="Mozilla/5.0")
        self.user = User.objects.create_user("pro@example.com",
                                             email="pro@example.com")
        self.listing = Property.objects.create(
            url="https://example.com/preview", title="A house in Oita",
            price=2249, floor_plan="3LDK", location="Oita Prefecture",
            construction_date="1976年7月（築49年）",
            city_planning="Urbanization control area", equipment="",
            land_rights="Ownership", land_category="Residence",
        )

    def page(self):
        return self.client.get(
            f"/japanese-houses/{self.listing.pk}/"
        ).content.decode()

    def test_the_panel_names_the_product_and_the_entitlement(self):
        """"Before you offer" described the moment, not the thing — nobody could
        learn what it was called."""
        body = self.page()
        self.assertIn("Desk report · included with Pro", body)

    def test_the_real_findings_are_shown_as_titles(self):
        body = self.page()
        self.assertIn("Inside an urbanization control area", body)
        self.assertIn("Water, sewer and gas are not disclosed", body)
        self.assertIn("before the current earthquake standard", body)

    def test_the_reasoning_is_not_in_the_page_at_all(self):
        """Withheld, not merely hidden: the explanation is what Pro buys."""
        body = self.page()
        self.assertNotIn("designated to stay undeveloped", body)
        self.assertNotIn("It needs the city's planning department", body)

    def test_a_visitor_without_pro_is_offered_pro(self):
        body = self.page()
        self.assertIn("Unlock with Pro", body)
        # The form, not its label: the button's text also lives in the script
        # that handles the claim, which is on the page for every tier.
        self.assertNotIn('id="drSubmit"', body)

    def test_a_pro_member_is_offered_the_report(self):
        Subscription.objects.create(
            user=self.user, paypal_subscription_id="I-PREVIEW",
            status=Subscription.STATUS_ACTIVE,
            current_period_end=timezone.now() + timedelta(days=20),
        )
        self.client.force_login(self.user)
        body = self.page()
        self.assertIn("Get the full report on this house", body)
        # Collapse whitespace: the template wraps this sentence over four lines.
        import re as _re
        self.assertIn("3 of this month's 3 left",
                      _re.sub(r"\s+", " ", body))

    def test_a_member_who_already_asked_sees_that_instead(self):
        from membership.models import DeskReportRequest

        Subscription.objects.create(
            user=self.user, paypal_subscription_id="I-PREVIEW2",
            status=Subscription.STATUS_ACTIVE,
            current_period_end=timezone.now() + timedelta(days=20),
        )
        DeskReportRequest.objects.create(
            user=self.user, email=self.user.email, listing=self.listing,
        )
        self.client.force_login(self.user)
        body = self.page()
        # Literal template text, so it is not HTML-escaped the way a variable
        # would be.
        self.assertIn("preparing your report on this property", body)
        self.assertNotIn('id="drSubmit"', body)

    def test_the_claim_form_asks_for_nothing(self):
        """One button. An optional field asks a question the member does not have
        to answer, right at the action we want them to take."""
        Subscription.objects.create(
            user=self.user, paypal_subscription_id="I-NOFIELD",
            status=Subscription.STATUS_ACTIVE,
            current_period_end=timezone.now() + timedelta(days=20),
        )
        self.client.force_login(self.user)
        body = self.page()
        self.assertIn('id="drSubmit"', body)
        self.assertNotIn('id="drNotes"', body)
        # Scoped to this form: the inspection panel below keeps its own optional
        # field, which is a lead form where the context is worth asking for.
        form = body[body.index('id="deskReportForm"'):]
        form = form[:form.index("</form>")]
        self.assertNotIn("(optional)", form)
        self.assertNotIn("<textarea", form)

    def test_withheld_rows_say_the_list_is_not_the_whole_report(self):
        """Faded rows beneath the findings. Each names something the report
        genuinely contains — a blurred placeholder implying findings we have not
        made would be a promise, not a teaser."""
        body = self.page()
        self.assertIn("pp-dr-more", body)
        self.assertIn("How this asking price compares", body)
        self.assertIn("What the municipal office says", body)
        # A separate list, because the fade is a mask on the container — masking
        # the findings list would make the real findings look withheld too.
        # Compare the markup, not the stylesheet — both class names appear in the
        # <style> block first, so searching the whole document finds the CSS.
        findings_list = body.index('<ul class="pp-dr-findings">')
        withheld_list = body.index('<ul class="pp-dr-findings pp-dr-more-list"')
        self.assertLess(findings_list, withheld_list,
                        "the real findings come first, then the faded rows")

    def test_the_withheld_rows_are_not_counted_as_findings(self):
        """The heading counts findings; these are sections, so a member must not
        read "7 things" and then find two of them were locked rows."""
        from inventory.desk_report import preview

        report = preview(self.listing)
        titles = [t["title"] for t in report["titles"]]
        for row in report["locked"]:
            self.assertNotIn(row["title"], titles)

    def test_the_finding_count_is_hedged(self):
        """The preview leaves out the rule that scans the prefecture, and the
        full report adds more — so the count is a floor, not a total."""
        self.assertIn("We found at least", self.page())

    def test_the_page_embeds_a_complete_report_on_another_house(self):
        """The warnings are about this house; the embed shows what the finished
        article looks like. Lazy and inside <details>, so it costs nothing until
        asked for."""
        body = self.page()
        self.assertIn("See a complete report, on another house", body)
        self.assertIn('src="/desk-report/example/"', body)
        self.assertIn('loading="lazy"', body)

    def test_a_sparse_listing_still_has_something_to_report(self):
        """A listing that publishes almost nothing is not a listing with nothing
        to say about it — the silence is itself the finding, and the panel should
        show it rather than going quiet on the least-documented houses."""
        blank = Property.objects.create(
            url="https://example.com/blank", title="Nothing known", price=500,
            floor_plan="1K", location="Oita Prefecture",
        )
        body = self.client.get(f"/japanese-houses/{blank.pk}/").content.decode()
        self.assertIn('id="deskreport"', body)
        self.assertIn("Water, sewer and gas are not disclosed", body)


@override_settings(ALLOWED_HOSTS=["testserver"], DESK_REPORT_SAMPLE_PK=0,
                   DESK_REPORT_PRO_ALLOWANCE=3)
class DeskReportExampleTests(TestCase):
    """The worked example — the page the property panel frames.

    It is the only proof a visitor has of what Pro buys, so it renders the real
    generator rather than a mock-up, and the framing header matters as much as
    the content: without it the embed shows "refused to connect" and nothing
    about the page itself looks wrong.
    """

    def setUp(self):
        self.client = Client(HTTP_USER_AGENT="Mozilla/5.0")
        from django.core.cache import cache
        cache.clear()
        self.listing = Property.objects.create(
            url="https://www.homes.co.jp/kodate/example/",
            title="Detached house in Yokose, Oita City", price=2249,
            floor_plan="3LDK", location="Yokoze, Oita City, Oita Prefecture",
            building_area="110.78㎡", land_area="289.85㎡",
            construction_date="1976年7月（築49年）",
            city_planning="Urbanization control area",
            land_rights="Ownership", land_category="Residence", equipment="",
            road_condition="East 5.8m private road", handover="July 2025",
        )

    def test_it_permits_being_framed_by_our_own_pages(self):
        """Django's clickjacking default is DENY, which refuses even same-origin
        frames. This is the header that makes the embed work at all."""
        page = self.client.get("/desk-report/example/")
        self.assertEqual(page.headers["X-Frame-Options"], "SAMEORIGIN")

    def test_the_framing_header_survives_the_page_cache(self):
        self.client.get("/desk-report/example/")
        cached = self.client.get("/desk-report/example/")
        self.assertEqual(cached.headers["X-Frame-Options"], "SAMEORIGIN")

    def test_it_is_the_real_generator_output(self):
        body = self.client.get("/desk-report/example/").content.decode()
        self.assertIn("Detached house in Yokose", body)
        self.assertIn("Inside an urbanization control area", body)
        self.assertIn("都市計画", body)
        self.assertIn("Take these to the agent", body)

    def test_it_opens_with_a_verdict_composed_from_the_findings(self):
        body = self.client.get("/desk-report/example/").content.decode()
        self.assertIn("whether the city will permit a dwelling to be rebuilt",
                      body)

    def test_it_reads_as_finished_not_as_a_draft(self):
        body = self.client.get("/desk-report/example/").content.decode()
        self.assertIn("Example report", body)
        self.assertNotIn("Not for issue", body)
        self.assertIn("What a person adds to this", body)

    def test_it_survives_an_empty_inventory(self):
        Property.objects.all().delete()
        from django.core.cache import cache
        cache.clear()
        self.assertEqual(self.client.get("/desk-report/example/").status_code, 200)

    def test_a_pinned_sample_wins(self):
        other = Property.objects.create(
            url="https://example.com/pinned", title="Pinned example house",
            price=800, floor_plan="2DK", location="Oita Prefecture",
        )
        from django.core.cache import cache
        cache.clear()
        with override_settings(DESK_REPORT_SAMPLE_PK=other.pk):
            body = self.client.get("/desk-report/example/").content.decode()
        self.assertIn("Pinned example house", body)

    def test_the_offer_page_states_the_monthly_allowance(self):
        body = self.client.get("/desk-report/").content.decode()
        self.assertIn("3 reports", body)
        self.assertNotIn("US$39", body)
