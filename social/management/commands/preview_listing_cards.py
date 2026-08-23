"""Render a listing carousel to disk without posting it.

The cards are the first thing anyone sees of a listing post, and there is no
other way to look at one: the reel pipeline posts straight to Instagram, and a
draft row holds paths nobody opens. So: render, print the paths, post nothing.
"""

import os

from django.core.management.base import BaseCommand

from inventory.models import Property
from social.constants import (
    LISTING_CARDS_ADD_SUMMARY,
    LISTING_CARDS_MAX_PHOTOS,
    PRICE_LIMIT_INSTAGRAM,
)
from social.models import SocialPost
from social.utils import (
    _card_location,
    _clean_area,
    _download_image_to_tempfile,
    prepare_image_url_for_facebook,
    select_properties_to_post,
)


class Command(BaseCommand):
    help = "Render a listing's carousel cards to a directory, without posting."

    def add_arguments(self, parser):
        parser.add_argument(
            "--pk", type=int,
            help="Property to render. Defaults to whatever the Instagram queue "
                 "would post next.",
        )
        parser.add_argument(
            "--out", default="preview_cards",
            help="Directory to write the JPEGs to (default: ./preview_cards).",
        )

    def handle(self, *args, **options):
        from social.content.listing_cards import render_listing_cards

        if options["pk"]:
            property = Property.objects.get(pk=options["pk"])
        else:
            candidates = select_properties_to_post(
                SocialPost.objects.filter(social_media="instagram"),
                price_limit=PRICE_LIMIT_INSTAGRAM,
                limit=1,
            )
            if not candidates:
                self.stderr.write(self.style.ERROR("Nothing in the queue to render."))
                return
            property = candidates[0]

        self.stdout.write(f"Rendering {property.pk}: {property.title}")

        temp_paths = []
        for image in property.get_ordered_images()[:LISTING_CARDS_MAX_PHOTOS]:
            url = prepare_image_url_for_facebook(image.file.url)
            try:
                temp_paths.append(_download_image_to_tempfile(url))
            except Exception as exc:
                self.stderr.write(self.style.WARNING(f"Skipped {url}: {exc}"))

        if not temp_paths:
            self.stderr.write(self.style.ERROR("No photo loaded — nothing to draw."))
            return

        try:
            paths = render_listing_cards(
                temp_paths,
                price=property.get_price_for_front,
                location=_card_location(property),
                building_area=_clean_area(property.building_area),
                land_area=_clean_area(property.land_area),
                link=f"www.akiyainjapan.com{property.get_public_url}",
                out_dir=options["out"],
                slug=f"preview-{property.pk}",
                add_summary=LISTING_CARDS_ADD_SUMMARY,
            )
        finally:
            for path in temp_paths:
                try:
                    os.remove(path)
                except OSError:
                    pass

        for path in paths:
            self.stdout.write(self.style.SUCCESS(os.path.abspath(path)))
