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
from membership.models import Consultation, PropertyView, Subscription

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
