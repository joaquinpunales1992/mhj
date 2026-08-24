"""Tests for the pages that are not listings.

The legal pages are here because of how they fail: not with an exception, but
with a 404 at a URL that a payment provider, an app reviewer or a regulator is
the one to discover. A template rename would do it silently.
"""

from django.test import TestCase, override_settings


class SiteVerificationTests(TestCase):
    """The file a domain-verification check fetches from the root.

    Worth testing because both failure modes are quiet: an unset filename that
    serves nothing, and a route loose enough to serve whatever is asked for.
    """

    FILE = "tiktokAbc123.txt"
    CONTENT = "tiktok-developers-site-verification=Abc123"

    def test_the_configured_file_is_served_as_plain_text(self):
        with override_settings(SITE_VERIFICATION_FILENAME=self.FILE,
                               SITE_VERIFICATION_CONTENT=self.CONTENT):
            response = self.client.get(f"/{self.FILE}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), self.CONTENT)
        self.assertEqual(response["Content-Type"], "text/plain")

    def refuses(self, url):
        """Not served, whatever the site does with an unknown URL.

        Asserting a 404 here would be asserting the wrong thing: this site's
        handler404 redirects unknown URLs to the home page, so a miss is a 302
        and always was. What matters is that the content does not come back.
        """
        response = self.client.get(url)
        self.assertNotEqual(response.status_code, 200, url)
        if response.status_code == 200:
            self.assertNotIn(self.CONTENT, response.content.decode())

    def test_any_other_filename_is_not_served(self):
        """Not a general-purpose way to serve text off the domain root."""
        with override_settings(SITE_VERIFICATION_FILENAME=self.FILE,
                               SITE_VERIFICATION_CONTENT=self.CONTENT):
            self.refuses("/something-else.txt")

    def test_nothing_is_served_when_it_is_not_configured(self):
        with override_settings(SITE_VERIFICATION_FILENAME="",
                               SITE_VERIFICATION_CONTENT=""):
            self.refuses(f"/{self.FILE}")

    def test_the_route_does_not_shadow_a_real_page(self):
        """It sits at the root, so it must not swallow the rest of the site."""
        for url in ("/terms/", "/privacy/", "/faqs/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)


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

    def test_the_privacy_page_still_names_who_data_is_shared_with(self):
        """The page is deliberately general, but not about this.

        Naming the recipients is the part a vague policy gets wrong and the part
        that is actually required. If a provider is swapped out, this failing is
        the reminder that the page needs updating too.
        """
        body = self.client.get("/privacy/").content.decode()
        for claim in ("Google", "PayPal", "hello@akiyainjapan.com"):
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
            self.assertNotIn("Governing law", body)
            self.assertIn("hello@akiyainjapan.com", body)

    def test_the_jurisdiction_and_its_heading_show_once_chosen(self):
        with override_settings(LEGAL_GOVERNING_LAW="Governed by the law of Japan."):
            response = self.client.get("/terms/")
            self.assertContains(response, "Governed by the law of Japan.")
            self.assertContains(response, "Governing law and contact")
