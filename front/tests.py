"""Tests for the pages that are not listings.

The legal pages are here because of how they fail: not with an exception, but
with a 404 at a URL that a payment provider, an app reviewer or a regulator is
the one to discover. A template rename would do it silently.
"""

from django.test import TestCase, override_settings


class LegalPageTests(TestCase):

    def test_the_terms_page_renders(self):
        response = self.client.get("/terms/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Terms of service")

    def test_the_privacy_page_renders(self):
        response = self.client.get("/privacy/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Privacy policy")

    def test_both_are_reachable_from_an_ordinary_page(self):
        """A policy nobody can find is most of the way to not having one."""
        body = self.client.get("/faqs/").content.decode()
        self.assertIn("/privacy/", body)
        self.assertIn("/terms/", body)

    def test_the_privacy_page_names_what_it_actually_collects(self):
        """These are the specifics that make it a policy rather than a template.

        If one of them stops being true — analytics removed, PayPal replaced —
        this test failing is the reminder to update the page.
        """
        body = self.client.get("/privacy/").content.decode()
        for claim in ("Google Analytics", "G-QBY9Y9CMPS", "PayPal", "hello@akiyainjapan.com"):
            self.assertIn(claim, body, f"the privacy policy no longer mentions {claim}")

    def test_no_operator_is_named_until_one_is_configured(self):
        """Better to say less than to name the wrong company."""
        with override_settings(LEGAL_ENTITY=""):
            self.assertNotContains(self.client.get("/privacy/"), "It is operated by")

    def test_the_operator_is_named_once_configured(self):
        with override_settings(LEGAL_ENTITY="Example K.K."):
            self.assertContains(self.client.get("/privacy/"), "Example K.K.")

    def test_no_jurisdiction_is_claimed_until_one_is_chosen(self):
        """And the heading does not promise a clause that is not there."""
        with override_settings(LEGAL_GOVERNING_LAW=""):
            body = self.client.get("/terms/").content.decode()
            self.assertIn("11. Contact", body)
            self.assertNotIn("Law and contact", body)
            self.assertIn("hello@akiyainjapan.com", body)

    def test_the_jurisdiction_and_its_heading_show_once_chosen(self):
        with override_settings(LEGAL_GOVERNING_LAW="Governed by the law of Japan."):
            response = self.client.get("/terms/")
            self.assertContains(response, "Governed by the law of Japan.")
            self.assertContains(response, "Law and contact")
