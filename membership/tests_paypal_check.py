"""Tests for the PayPal configuration check.

It exists to explain a blank PayPal window, so what matters is that each cause
produces a message naming that cause. No network: every call is mocked.
"""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings


def response(status, payload=None, text=""):
    class _Response:
        status_code = status

        def json(self):
            return payload or {}
    _Response.text = text
    return _Response()


class PayPalCheckTests(SimpleTestCase):

    def run_check(self):
        out = StringIO()
        call_command("paypal_check", stdout=out, stderr=out)
        return out.getvalue()

    @override_settings(PAYPAL_CLIENT_ID="", PAYPAL_PLAN_ID="")
    def test_unset_billing_says_there_is_no_button_at_all(self):
        """Not a broken button — the page shows the waitlist instead."""
        self.assertIn("must both be set", self.run_check())

    @override_settings(PAYPAL_CLIENT_ID="id", PAYPAL_CLIENT_SECRET="s",
                       PAYPAL_PLAN_ID="P-1", PAYPAL_ENVIRONMENT="live")
    def test_rejected_credentials_name_the_environment(self):
        with patch("membership.management.commands.paypal_check._access_token",
                   return_value=None):
            output = self.run_check()
        self.assertIn("Could not get a token", output)
        self.assertIn("other environment", output)

    @override_settings(PAYPAL_CLIENT_ID="id", PAYPAL_CLIENT_SECRET="s",
                       PAYPAL_PLAN_ID="P-MISSING")
    def test_a_plan_from_the_other_environment_is_named_as_such(self):
        """The commonest cause of the blank window."""
        with patch("membership.management.commands.paypal_check._access_token",
                   return_value="t"), \
             patch("membership.management.commands.paypal_check.requests.get",
                   return_value=response(404)):
            output = self.run_check()
        self.assertIn("does not exist in", output)
        self.assertIn("blank PayPal window", output)

    @override_settings(PAYPAL_CLIENT_ID="id", PAYPAL_CLIENT_SECRET="s",
                       PAYPAL_PLAN_ID="P-1")
    def test_an_inactive_plan_is_reported(self):
        plan = {"name": "Pro", "status": "INACTIVE", "billing_cycles": []}
        with patch("membership.management.commands.paypal_check._access_token",
                   return_value="t"), \
             patch("membership.management.commands.paypal_check.requests.get",
                   return_value=response(200, plan)):
            output = self.run_check()
        self.assertIn("not ACTIVE", output)

    @override_settings(PAYPAL_CLIENT_ID="id", PAYPAL_CLIENT_SECRET="s",
                       PAYPAL_PLAN_ID="P-1")
    def test_a_working_setup_says_so_and_points_at_the_browser(self):
        plan = {"name": "Pro", "status": "ACTIVE", "billing_cycles": [
            {"pricing_scheme": {"fixed_price": {"value": "10.00",
                                                "currency_code": "USD"}}}]}
        with patch("membership.management.commands.paypal_check._access_token",
                   return_value="t"), \
             patch("membership.management.commands.paypal_check.requests.get",
                   return_value=response(200, plan)):
            output = self.run_check()
        self.assertIn("plan is active", output)
        self.assertIn("10.00 USD", output)
