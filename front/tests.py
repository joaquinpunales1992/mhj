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


class PropertyPreviewTests(TestCase):
    """The home page popup's contents.

    The reason this is a view and not a template include is metering: a popup
    that showed the detail without recording the view would let anyone read
    every listing from the home page and never meet the wall. Most of what is
    below is that, checked from the outside.
    """

    def setUp(self):
        self.property = self.listing("https://example.com/a")

    def listing(self, url, photos=6):
        from inventory.models import Property, PropertyImage

        listing = Property.objects.create(
            url=url, price=200, show_in_front=True,
            location="Oita Prefecture, Bungo-ono City",
            building_area="78.5㎡", land_area="198.73㎡", floor_plan="3DK",
        )
        for index in range(photos):
            PropertyImage.objects.create(
                property=listing, file=f"https://img.example.com/{index}.jpg"
            )
        return listing

    def preview(self, listing=None):
        return self.client.get(f"/japanese-houses/{(listing or self.property).pk}/preview/")

    def test_it_renders_the_price_the_place_and_the_facts(self):
        response = self.preview()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "US$14,000")
        self.assertContains(response, "Bungo-ono City")
        self.assertContains(response, "3DK")

    def test_it_shows_several_photos(self):
        """The whole point of the redesign: one photo was all poptrox could do."""
        self.assertContains(self.preview(), 'class="pv-shot', count=6)

    def test_a_missing_listing_is_a_404_not_the_whole_home_page(self):
        """handler404 on this site redirects home, and the popup follows it.

        Raising Http404 here would inject the entire home page into the modal.
        """
        response = self.client.get("/japanese-houses/999999/preview/")
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, "data-preview=", status_code=404)

    def test_opening_the_popup_spends_a_view(self):
        """It is a detail page in a box, and it has to cost the same."""
        with override_settings(VIEW_LIMIT_ANONYMOUS=2):
            self.preview(self.listing("https://example.com/1"))
            self.preview(self.listing("https://example.com/2"))
            response = self.preview(self.listing("https://example.com/3"))

        self.assertContains(response, "You have opened all")
        self.assertNotContains(response, "<dt>Layout</dt>")

    def test_the_wall_offers_the_next_step_rather_than_just_refusing(self):
        with override_settings(VIEW_LIMIT_ANONYMOUS=1):
            self.preview(self.listing("https://example.com/1"))
            response = self.preview(self.listing("https://example.com/2"))
        self.assertContains(response, "Create a free account")

    def test_reopening_the_same_listing_does_not_spend_another_view(self):
        """Otherwise closing and reopening a popup would burn the allowance."""
        with override_settings(VIEW_LIMIT_ANONYMOUS=2):
            self.preview()
            self.preview()
            response = self.preview()
        self.assertNotContains(response, "You have opened all")
        self.assertContains(response, "<dt>Layout</dt>")

    def test_the_price_and_the_place_survive_the_wall(self):
        """They were on the card already; hiding them would read as a bug."""
        with override_settings(VIEW_LIMIT_ANONYMOUS=1):
            self.preview(self.listing("https://example.com/1"))
            response = self.preview(self.listing("https://example.com/2"))
        self.assertContains(response, "US$14,000")

    def test_the_gallery_is_capped_once_the_allowance_is_spent(self):
        with override_settings(VIEW_LIMIT_ANONYMOUS=1, VIEW_PHOTO_LIMIT_LOCKED=3):
            self.preview(self.listing("https://example.com/1"))
            response = self.preview(self.listing("https://example.com/2"))
        self.assertContains(response, 'class="pv-shot', count=3)
        self.assertContains(response, "more photo")

    def test_a_visitor_is_asked_to_sign_in_for_a_desk_report(self):
        """Rather than being offered a button that will refuse them."""
        response = self.preview()
        self.assertContains(response, "Sign in to request a desk report")
        self.assertNotContains(response, 'class="pv-desk"')

    def test_a_member_gets_the_desk_report_button(self):
        from django.contrib.auth.models import User

        self.client.force_login(
            User.objects.create_user("m", "m@example.com", "pw")
        )
        self.assertContains(self.preview(), "pv-desk")

    def test_the_card_links_carry_the_hook_the_popup_binds_to(self):
        """The home page opens this; without data-preview nothing would."""
        self.assertContains(self.client.get("/"), "data-preview=")


class SharedPopupTests(TestCase):
    """One popup, both pages.

    The map used to open a 240px Leaflet card with one photo and a link. It
    opens the same popup the home page does now, from the same include — two
    implementations of the same thing is how they drift.
    """

    def setUp(self):
        from inventory.models import Property, PropertyImage

        self.property = Property.objects.create(
            url="https://example.com/a", price=200, show_in_front=True,
            location="Oita Prefecture, Bungo-ono City", floor_plan="3DK",
        )
        PropertyImage.objects.create(
            property=self.property, file="https://img.example.com/1.jpg"
        )

    def test_the_home_page_carries_the_popup(self):
        self.assertContains(self.client.get("/"), 'id="pv-overlay"')

    def test_the_map_carries_the_same_popup(self):
        self.assertContains(self.client.get("/map/"), 'id="pv-overlay"')

    def test_the_map_cards_open_it(self):
        """Without data-preview the card is just a link, and navigates away."""
        self.assertContains(self.client.get("/map/"), "data-preview=")

    def test_the_map_marker_link_opens_it_too(self):
        """Picking a property off the map is picking a marker, not a card."""
        body = self.client.get("/map/").content.decode()
        self.assertIn("pop-link", body)
        self.assertIn("data-preview=\"' + encodeURIComponent(p.i)", body)

    def test_the_map_loads_the_unit_script(self):
        """The popup's price and areas answer the same switches as everywhere."""
        self.assertContains(self.client.get("/map/"), "units.js")

    def test_the_popup_offers_save_and_share_on_the_photo(self):
        """The pair the full listing page overlays on its gallery."""
        body = self.client.get(f"/japanese-houses/{self.property.pk}/preview/").content.decode()
        self.assertIn("pv-media-actions", body)
        self.assertIn("pv-share", body)

    def test_the_popup_sizes_its_own_icons(self):
        """The pin ships without dimensions and rendered 392px on the map."""
        body = self.client.get(f"/japanese-houses/{self.property.pk}/preview/").content.decode()
        self.assertIn(".pv-place svg", body)
