"""Tests for the parts of the social bot that decide reach.

Nothing here talks to Meta or encodes video. What is worth covering is the
bookkeeping that makes the bot answerable — the published id being stored, the
insights landing on the right row, and averages that exclude what was never
measured — plus the two caption/hook rules that changed, because a silent
regression there costs reach without breaking anything.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from inventory.models import Property, PropertyImage
from social.constants import PRICE_LIMIT_INSTAGRAM, REPOST_COOLDOWN_DAYS
from social.models import SocialComment, SocialPost


def response(status=200, payload=None):
    """A stand-in for requests' response object."""

    class _Response:
        status_code = status
        text = json.dumps(payload or {})

        def json(self):
            return payload or {}

    return _Response()


def insights_payload(**metrics):
    return {"data": [{"name": name, "values": [{"value": value}]}
                     for name, value in metrics.items()]}


class CaptionTests(TestCase):
    """The caption's first line is the only one most people read."""

    def caption(self, price="US$18,000", location="Oita Prefecture", **kwargs):
        from social.utils import generate_caption_for_post

        defaults = dict(
            property_location=location,
            property_url="/japanese-houses/1/",
            property_price=price,
            property_building_area="78.5m 2",
            property_land_area="198.73m 2 （60.11坪）",
            last_caption_generated="",
            use_ai_caption=False,
        )
        defaults.update(kwargs)
        return generate_caption_for_post(**defaults)

    def test_the_caption_opens_with_the_price_and_the_place(self):
        _, caption, _ = self.caption()
        self.assertTrue(
            caption.startswith("US$18,000 · Oita Prefecture"),
            f"caption led with: {caption.splitlines()[0]!r}",
        )

    def test_the_price_is_not_repeated_below(self):
        """It used to appear as the lead and again in the details block."""
        _, caption, _ = self.caption()
        self.assertEqual(caption.count("US$18,000"), 1)

    def test_the_details_and_hashtags_survive(self):
        _, caption, _ = self.caption()
        self.assertIn("🏡 Building: 78.5 m²", caption)
        self.assertIn("🌳 Land: 198.73 m² (60.11 tsubo)", caption)
        self.assertIn("www.akiyainjapan.com/japanese-houses/1/", caption)
        self.assertIn("#akiya", caption)
        self.assertIn("#oita", caption, "location hashtag should be derived")

    def test_the_angle_comes_back_so_it_can_be_stored(self):
        with patch("social.utils.ai_client") as ai:
            ai.return_value.generate_text.return_value = "A hook\n\nBody copy."
            ai_caption, caption, angle = self.caption(use_ai_caption=True)
        self.assertTrue(angle, "the creative direction used must be reported")
        self.assertIn("A hook", ai_caption)
        self.assertTrue(caption.startswith("US$18,000 · Oita Prefecture"))

    def test_a_dead_ai_still_produces_a_price_led_caption(self):
        with patch("social.utils.ai_client") as ai:
            ai.return_value.generate_text.side_effect = RuntimeError("no service")
            _, caption, angle = self.caption(use_ai_caption=True)
        self.assertTrue(caption.startswith("US$18,000 · Oita Prefecture"))
        self.assertEqual(angle, "", "no angle was used, so none may be recorded")

    def test_a_missing_location_does_not_leave_a_dangling_separator(self):
        _, caption, _ = self.caption(location="")
        self.assertTrue(caption.startswith("US$18,000\n"))


class AreaCleaningTests(TestCase):
    """The scraper machine-translates Japanese measurement notes, sometimes
    absurdly, and every one of them was reaching live captions."""

    def clean(self, value):
        from social.utils import _clean_area

        return _clean_area(value)

    def test_the_measurement_basis_notes_are_dropped(self):
        # 公簿 / 内法 / 実測 — the last one arriving as "crystal".
        self.assertEqual(self.clean("101㎡ (public book)"), "101 m²")
        self.assertEqual(self.clean("103.24㎡ (crystal)"), "103.24 m²")
        self.assertEqual(self.clean("103.09㎡ (internal method)"), "103.09 m²")
        self.assertEqual(self.clean("230.56㎡ (actual measurement)"), "230.56 m²")
        self.assertEqual(self.clean("93.96m 2 （登記）"), "93.96 m²")

    def test_an_unseen_mistranslation_is_dropped_too(self):
        """The rule is 'a parenthetical with no digits is a note', so the next
        invented translation needs no code change."""
        self.assertEqual(self.clean("88.5㎡ (moon language)"), "88.5 m²")

    def test_the_tsubo_figure_survives_including_as_ping(self):
        """It carries a number, which is exactly what distinguishes it."""
        self.assertEqual(
            self.clean("72.71m 2 （21.99坪）（登記）"), "72.71 m² (21.99 tsubo)"
        )
        self.assertEqual(
            self.clean("65.62m 2 (19.84 ping)"), "65.62 m² (19.84 tsubo)"
        )

    def test_the_points_tail_is_dropped(self):
        """Carries digits, so the digit-free rule cannot catch it."""
        self.assertEqual(
            self.clean("267.65㎡ (public book) has a total of 1/4 points"), "267.65 m²"
        )

    def test_the_unit_spellings_all_normalise(self):
        for raw in ("32.35m2", "51.14m 2", "103㎡", "174.03 m2"):
            self.assertRegex(self.clean(raw), r"^[\d.]+ m²$", raw)

    def test_an_empty_value_stays_empty(self):
        self.assertEqual(self.clean(""), "")
        self.assertEqual(self.clean(None), "")


class InsightsFetchTests(TestCase):
    """Reading the numbers back, including when Meta renames the metrics."""

    def setUp(self):
        self.post = SocialPost.objects.create(
            caption="x", social_media="instagram", content_type="reel",
            media_id="17900000000000000",
        )

    def test_the_snapshot_lands_on_the_post(self):
        from social.insights import refresh_post_insights

        payload = insights_payload(
            reach=4210, views=9100, likes=180, comments=12, saved=95, shares=40,
            total_interactions=327, ig_reels_avg_watch_time=5400,
        )
        with patch("social.insights.requests.get", return_value=response(200, payload)):
            self.assertTrue(refresh_post_insights(self.post, "token"))

        self.post.refresh_from_db()
        self.assertEqual(self.post.views, 9100)
        self.assertEqual(self.post.reach, 4210)
        self.assertEqual(self.post.saves, 95)
        self.assertEqual(self.post.comments_count, 12)
        self.assertEqual(self.post.avg_watch_time_ms, 5400)
        self.assertIsNotNone(self.post.insights_fetched_at)
        self.assertAlmostEqual(self.post.engagement_rate, 100 * 327 / 4210)

    def test_an_unsupported_metric_is_dropped_and_the_rest_still_arrive(self):
        """Meta renamed plays to views. Pinning to one spelling means that the
        day it changes, the report silently goes blank."""
        from social.insights import refresh_post_insights

        # Meta names the *position* it objected to and then lists what it does
        # accept — index 2 is `plays` in REEL_METRICS.
        error = {"error": {"message": "(#100) metric[2] must be one of the "
                                      "following values: reach, views, likes, "
                                      "comments, saved, shares"}}
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append(params["metric"])
            if "plays" in params["metric"]:
                return response(400, error)
            return response(200, insights_payload(reach=100, views=500))

        with patch("social.insights.requests.get", side_effect=fake_get):
            self.assertTrue(refresh_post_insights(self.post, "token"))

        self.post.refresh_from_db()
        self.assertEqual(self.post.views, 500)
        self.assertEqual(len(calls), 2, "should retry once without the bad metric")
        self.assertNotIn("plays", calls[1])

    def test_an_allowed_metric_named_in_the_error_is_not_dropped(self):
        """The error lists the values Meta accepts. Treating those names as
        refusals would drop good metrics and report nothing."""
        from social.insights import _unsupported_metrics, REEL_METRICS

        refused = _unsupported_metrics(
            "(#100) metric[2] must be one of the following values: reach, views",
            REEL_METRICS,
        )
        self.assertEqual(refused, ["plays"])
        self.assertNotIn("reach", refused)

    def test_an_unrecognisable_error_falls_back_to_the_volatile_metrics(self):
        from social.insights import _unsupported_metrics, REEL_METRICS

        refused = _unsupported_metrics("(#1) An unknown error occurred", REEL_METRICS)
        self.assertEqual(refused, ["views", "plays", "ig_reels_avg_watch_time"])

    def test_plays_lands_in_the_views_column(self):
        """Older API versions only speak `plays`; it is the same fact."""
        from social.insights import refresh_post_insights

        with patch("social.insights.requests.get",
                   return_value=response(200, insights_payload(plays=777, reach=10))):
            refresh_post_insights(self.post, "token")
        self.post.refresh_from_db()
        self.assertEqual(self.post.views, 777)

    def test_a_post_with_no_media_id_is_left_alone(self):
        from social.insights import refresh_post_insights

        orphan = SocialPost.objects.create(caption="old", social_media="instagram")
        with patch("social.insights.requests.get") as get:
            self.assertFalse(refresh_post_insights(orphan, "token"))
        get.assert_not_called()
        self.assertIsNone(orphan.insights_fetched_at)

    def test_a_network_failure_is_not_fatal(self):
        import requests as requests_module
        from social.insights import refresh_insights

        with patch("social.insights.requests.get",
                   side_effect=requests_module.ConnectionError("down")):
            fetched, skipped, problems = refresh_insights([self.post], "token")
        self.assertEqual((fetched, skipped), (0, 1))
        self.assertEqual(problems, [], "a network drop is not a reportable refusal")
        self.post.refresh_from_db()
        self.assertIsNone(self.post.insights_fetched_at)

    def test_a_permission_error_is_not_retried_as_a_metric_problem(self):
        """(#10) is what a token that can publish but not read insights returns.
        Dropping metrics cannot fix it, and retrying buries the only useful
        sentence in the response."""
        from social.insights import refresh_post_insights

        error = {"error": {"code": 10, "message": "(#10) Application does not "
                                                 "have permission for this action"}}
        with patch("social.insights.requests.get",
                   return_value=response(400, error)) as get:
            self.assertFalse(refresh_post_insights(self.post, "token"))
        self.assertEqual(get.call_count, 1, "no point asking again")

    def test_the_refusal_reason_is_reported_once_per_kind(self):
        """One missing permission refuses every post identically — the operator
        needs the reason, not 900 copies of it."""
        from social.insights import refresh_insights

        other = SocialPost.objects.create(
            caption="y", social_media="instagram", content_type="reel", media_id="2",
        )
        error = {"error": {"code": 10, "message": "(#10) Application does not "
                                                 "have permission for this action"}}
        with patch("social.insights.requests.get", return_value=response(400, error)):
            fetched, skipped, problems = refresh_insights([self.post, other], "token")

        self.assertEqual((fetched, skipped), (0, 2))
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][1], 2, "counted, not repeated")

    def test_an_expired_token_is_not_retried_either(self):
        from social.insights import refresh_post_insights

        error = {"error": {"code": 190, "message": "Error validating access token"}}
        with patch("social.insights.requests.get",
                   return_value=response(400, error)) as get:
            self.assertFalse(refresh_post_insights(self.post, "token"))
        self.assertEqual(get.call_count, 1)

    def test_a_real_zero_is_kept_and_never_fetched_stays_null(self):
        """0 views is a result; NULL means nobody asked. Reporting them the same
        way is how an unmeasured post gets blamed for failing."""
        from social.insights import refresh_post_insights

        with patch("social.insights.requests.get",
                   return_value=response(200, insights_payload(views=0, reach=0))):
            refresh_post_insights(self.post, "token")
        self.post.refresh_from_db()
        self.assertEqual(self.post.views, 0)
        self.assertIsNone(self.post.likes)
        self.assertIsNone(self.post.engagement_rate)


class GroupingTests(TestCase):
    """The comparison the whole exercise is for."""

    def make(self, angle, views, fetched=True, **kwargs):
        return SocialPost.objects.create(
            caption="x", social_media="instagram", content_type="reel",
            media_id="1", caption_angle=angle, views=views,
            insights_fetched_at=timezone.now() if fetched else None,
            **kwargs,
        )

    def test_unmeasured_posts_do_not_drag_an_angle_down(self):
        from social.insights import group_by

        self.make("lifestyle", 1000)
        self.make("lifestyle", 3000)
        self.make("lifestyle", None, fetched=False)
        rows = group_by(SocialPost.objects.all(), "caption_angle")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["posts"], 2)
        self.assertEqual(rows[0]["views"], 2000)

    def test_the_best_angle_comes_first(self):
        from social.insights import group_by

        self.make("value", 9000)
        self.make("lifestyle", 100)
        rows = group_by(SocialPost.objects.all(), "caption_angle")
        self.assertEqual([r["label"] for r in rows], ["value", "lifestyle"])


class ReportCommandTests(TestCase):
    """The report has to be readable before it is measured, and must never
    pretend that unmeasurable history simply doesn't exist."""

    def run_command(self, *args):
        out = StringIO()
        call_command("reel_insights", "--no-fetch", *args, stdout=out)
        return out.getvalue()

    def test_it_says_how_much_of_the_history_can_never_be_measured(self):
        SocialPost.objects.create(caption="old one", social_media="instagram",
                                  content_type="reel")
        output = self.run_command()
        self.assertIn("can be measured", output)
        # The sentence wraps, so match its halves rather than the whole phrase.
        self.assertIn("published without a stored media id", output)
        self.assertIn("can never", output)

    def test_it_reports_the_angles_and_the_best_posts(self):
        property = Property.objects.create(
            url="https://example.com/a", price=1500, show_in_front=True,
        )
        SocialPost.objects.create(
            caption="US$1,500 · Oita\n\nhook", social_media="instagram",
            content_type="reel", media_id="1", caption_angle="lead with the value",
            property_url=property.url, views=8000, reach=5000, saves=100,
            shares=20, total_interactions=400, avg_watch_time_ms=6200,
            sound_track="/static/audios_for_social_posts/zen-garden-310599.mp3",
            insights_fetched_at=timezone.now(),
        )
        output = self.run_command()
        self.assertIn("By caption angle", output)
        self.assertIn("lead with the value", output)
        self.assertIn("By soundtrack", output)
        self.assertIn("zen-garden-310599.mp3", output)
        self.assertIn("By price band", output)
        self.assertIn("$1k–2k", output)
        self.assertIn("6.2s", output, "watch time is the retention number")

    def test_it_does_not_call_meta_with_no_fetch(self):
        SocialPost.objects.create(caption="x", social_media="instagram",
                                  media_id="1", content_type="reel")
        with patch("social.insights.requests.get") as get:
            self.run_command()
        get.assert_not_called()

    def test_it_says_what_to_do_when_meta_refuses(self):
        """A refusal that only appears in the logs is a refusal nobody acts on."""
        SocialPost.objects.create(caption="x", social_media="instagram",
                                  content_type="reel", media_id="1")
        error = {"error": {"code": 10, "message": "(#10) Application does not "
                                                 "have permission for this action"}}
        out = StringIO()
        with patch("social.utils.get_fresh_token", return_value="token"), \
             patch("social.insights.requests.get", return_value=response(400, error)):
            call_command("reel_insights", stdout=out)
        output = out.getvalue()
        self.assertIn("refused (1x)", output)
        self.assertIn("instagram_manage_insights", output)
        self.assertIn("refresh_social_token", output)

    def test_an_empty_window_says_so_rather_than_erroring(self):
        SocialPost.objects.create(caption="x", social_media="instagram",
                                  media_id="1", content_type="reel")
        SocialPost.objects.update(datetime=timezone.now() - timedelta(days=400))
        self.assertIn("Nothing posted", self.run_command("--days", "30"))


class ReelPostingTests(TestCase):
    """What gets written down when a reel goes out.

    Mocked end to end — no encode, no HTTP — because the thing worth protecting
    is the bookkeeping. Without the published id the post cannot be measured
    afterwards, and that failure is invisible at posting time: everything looks
    like it worked.
    """

    def setUp(self):
        # featured, because the queue only serves featured listings now.
        self.property = Property.objects.create(
            url="https://example.com/house-1", price=1800, show_in_front=True,
            featured=True, location="Oita Prefecture, Bungo-ono City",
            floor_plan="3DK", building_area="78.5m 2", land_area="198.73m 2",
        )
        PropertyImage.objects.create(property=self.property, file="properties/a.jpg")

    def post_reel(self, publish_id="17999999999999999"):
        from social import utils

        def fake_post(url, data=None, **kwargs):
            if url.endswith("/media"):
                self.media_payload = data
                return response(200, {"id": "container-1"})
            if url.endswith("/media_publish"):
                return response(200, {"id": publish_id})
            return response(200, {"id": "comment-1"})

        with patch.object(utils, "create_property_video",
                          side_effect=self.fake_video) as video, \
             patch.object(utils, "_get_random_mp3_full_path",
                          return_value="/audios/zen-garden-310599.mp3"), \
             patch.object(utils, "get_fresh_token", return_value="token"), \
             patch.object(utils.shutil, "move"), \
             patch.object(utils.time, "sleep"), \
             patch.object(utils, "ai_client") as ai, \
             patch.object(utils.requests, "post", side_effect=fake_post):
            ai.return_value.generate_text.return_value = "Wake Up Here"
            utils.post_instagram_reel()
        self.video_call = video

    def fake_video(self, property_id, output_path, audio_path,
                   duration_per_image=3, meta=None):
        """Stand in for the encoder, including the hook it reports back."""
        if meta is not None:
            meta["overlay_hook"] = "Wake Up Here"
        return output_path

    def test_the_published_id_is_stored(self):
        self.post_reel()
        post = SocialPost.objects.get()
        self.assertEqual(post.media_id, "17999999999999999")
        self.assertEqual(post.content_type, "reel")

    def test_the_angle_and_the_hook_are_stored_beside_it(self):
        self.post_reel()
        post = SocialPost.objects.get()
        self.assertTrue(post.caption_angle, "which angle was used must be recorded")
        self.assertEqual(post.overlay_hook, "Wake Up Here")

    def test_the_reel_is_shared_to_the_feed(self):
        """Reels-tab-only publishing gave up the feed and the profile grid."""
        self.post_reel()
        self.assertTrue(self.media_payload["share_to_feed"])

    def test_the_caption_sent_to_instagram_leads_with_the_price(self):
        """Not just built price-led — sent that way."""
        self.post_reel()
        lead = f"{self.property.get_price_for_front} · Oita Prefecture"
        self.assertTrue(self.media_payload["caption"].startswith(lead),
                        self.media_payload["caption"][:60])


class CommentReplyTests(TestCase):
    """A reply that ignores what was asked reads as a bot and ends the thread."""

    def test_the_comment_text_reaches_the_prompt(self):
        from social import social_bot

        with patch.object(social_bot, "_get_reels",
                          return_value=[{"id": "17900000000000000",
                                         "media_type": "VIDEO"}]), \
             patch.object(social_bot, "_get_comments_per_reel",
                          return_value=[{"id": "1801", "text": "How much is the "
                                                              "property tax?"}]), \
             patch.object(social_bot, "_reply_comment",
                          return_value={"id": "1802"}) as reply, \
             patch.object(social_bot, "ai_client") as ai:
            ai.return_value.generate_text.return_value = "It depends on the town — ask us!"
            social_bot.reply_comments_instagram()

        prompt = ai.return_value.generate_text.call_args.kwargs["prompt"]
        self.assertIn("How much is the property tax?", prompt)
        reply.assert_called_once()

        comment = SocialComment.objects.get()
        self.assertTrue(comment.replied)
        self.assertEqual(comment.comment, "It depends on the town — ask us!",
                         "the reply text belongs here, not the API's response object")


class SocialPhotoSelectionTests(TestCase):
    """Which photos go out, and which are left behind.

    Listings carry 間取り floor plans and 立面図 elevations among the photographs.
    A line drawing between two pictures of a house is what stops a scroll for
    the wrong reason.
    """

    def listing(self, photos):
        listing = Property.objects.create(
            url="https://example.com/a", price=200, show_in_front=True,
            featured=True, location="Oita Prefecture",
        )
        for i in range(photos):
            PropertyImage.objects.create(
                property=listing, file=f"https://img.example.com/{i}.jpg"
            )
        return listing

    def chosen(self, listing, limit=4):
        from social.utils import social_photos

        return [p.file.name for p in social_photos(listing, limit)]

    def test_every_photo_is_a_candidate_by_default(self):
        """Position is not the test; see looks_like_a_drawing for why."""
        listing = self.listing(4)
        every = [p.file.name for p in listing.get_ordered_images()]
        self.assertEqual(self.chosen(listing), every)

    def test_a_position_can_still_be_skipped_if_configured(self):
        from unittest.mock import patch
        from social import utils

        listing = self.listing(5)
        every = [p.file.name for p in listing.get_ordered_images()]
        with patch.object(utils, "SOCIAL_SKIP_PHOTO_POSITIONS", (1,)):
            self.assertEqual(self.chosen(listing, limit=4),
                             [every[0], every[2], every[3], every[4]])

    def test_a_few_extra_candidates_come_back(self):
        """Drawings are found after downloading, so dropping one there must not
        leave the post a photo short."""
        self.assertEqual(len(self.chosen(self.listing(20), limit=4)), 6)

    def test_a_listing_with_one_photo_keeps_it(self):
        self.assertEqual(len(self.chosen(self.listing(1))), 1)

    def test_skipping_never_empties_a_listing_that_has_photos(self):
        from unittest.mock import patch
        from social import utils

        listing = self.listing(2)
        with patch.object(utils, "SOCIAL_SKIP_PHOTO_POSITIONS", (0, 1)):
            self.assertEqual(len(self.chosen(listing)), 2)


class PhotoLabelTests(TestCase):
    """Photos the source told us are not the house.

    SUUMO appends a surroundings section to a listing's gallery and labels it —
    病院, 公園, スーパー, 駅 — while leaving the property's own photos
    unlabelled. In a sample of stored images 54% carried one of those labels.
    They are photographs of real places, so the plan detector cannot help: only
    the label separates the house from the hospital across the road.
    """

    def listing(self, labels):
        listing = Property.objects.create(
            url="https://suumo.jp/x", price=200, show_in_front=True,
            featured=True, location="Oita Prefecture",
        )
        for i, label in enumerate(labels):
            PropertyImage.objects.create(
                property=listing, file=f"https://img.example.com/{i}.jpg",
                label=label,
            )
        return listing

    def chosen(self, listing, limit=4):
        from social.utils import social_photos

        return [p.label for p in social_photos(listing, limit)]

    def test_the_neighbourhood_is_left_out(self):
        listing = self.listing(["", "病院", "", "公園", "スーパー", "駅"])
        self.assertEqual(self.chosen(listing), ["", ""])

    def test_the_agents_headshot_is_left_out(self):
        """担当者 — the person selling it, not the thing being sold."""
        self.assertEqual(self.chosen(self.listing(["担当者", ""])), [""])

    def test_the_propertys_own_equipment_is_kept(self):
        """その他設備 is labelled too, and it is the kitchen and the sink. A
        blanket 'drop anything labelled' would throw away good photographs."""
        listing = self.listing(["", "その他設備", "病院"])
        self.assertEqual(self.chosen(listing), ["", "その他設備"])

    def test_unlabelled_photos_are_all_kept(self):
        """Everything scraped before the field existed, which is most of it."""
        listing = self.listing(["", "", ""])
        self.assertEqual(len(self.chosen(listing)), 3)

    def test_a_listing_of_nothing_but_surroundings_is_still_postable(self):
        """Better a hospital than no post at all — and better still that the
        warning in the log says why."""
        listing = self.listing(["病院", "公園"])
        self.assertEqual(len(self.chosen(listing)), 2)


class DrawingDetectionTests(SimpleTestCase):
    """Telling a plan from a photograph, by its pixels.

    The threshold comes from measuring 96 real images across 20 listings: the
    12 drawings scored 0.40-0.79 paper white, every photograph 0.12 or less.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def drawing(self, name="plan.jpg"):
        """A floor plan: white, with a few black lines on it."""
        from PIL import Image, ImageDraw

        path = os.path.join(self.dir, name)
        canvas = Image.new("RGB", (600, 450), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)
        for box in [(60, 60, 300, 250), (300, 60, 540, 160), (60, 250, 540, 390)]:
            draw.rectangle(box, outline=(0, 0, 0), width=3)
        canvas.save(path, "JPEG", quality=92)
        return path

    def photograph(self, name="photo.jpg"):
        """A photograph: colour everywhere, almost no white."""
        from PIL import Image

        path = os.path.join(self.dir, name)
        canvas = Image.new("RGB", (600, 450))
        canvas.putdata([
            ((x * 7 + y * 3) % 200, (x * 3 + y * 11) % 190, (x + y * 5) % 210)
            for y in range(450) for x in range(600)
        ])
        canvas.save(path, "JPEG", quality=92)
        return path

    def looks_like_a_drawing(self, path):
        from social.constants import SOCIAL_DRAWING_PAPER_MIN
        from social.content.listing_cards import looks_like_a_drawing

        return looks_like_a_drawing(path, SOCIAL_DRAWING_PAPER_MIN)

    def test_a_plan_is_recognised(self):
        self.assertTrue(self.looks_like_a_drawing(self.drawing()))

    def test_a_photograph_is_not(self):
        self.assertFalse(self.looks_like_a_drawing(self.photograph()))

    def test_a_white_house_in_snow_is_not(self):
        """The case that sank the first version of this: a white building under
        a white sky measured 45% white and was a listing's opening photo. Sky
        and snow shade, and JPEG leaves them off neutral; paper does neither."""
        from PIL import Image

        path = os.path.join(self.dir, "snow.jpg")
        canvas = Image.new("RGB", (600, 450))
        canvas.putdata([
            # Near-white everywhere, but shading and never exactly neutral.
            (250 - y // 40, 252 - y // 50, 247 - y // 45)
            for y in range(450) for _ in range(600)
        ])
        canvas.save(path, "JPEG", quality=92)
        self.assertGreater(self.white_share(path), 0.40)
        self.assertFalse(self.looks_like_a_drawing(path))

    def white_share(self, path):
        """Plain whiteness — the metric this detector used to use."""
        from PIL import Image

        with Image.open(path) as raw:
            pixels = list(raw.convert("RGB").resize((160, 120)).getdata())
        white = sum(1 for r, g, b in pixels if r > 235 and g > 235 and b > 235)
        return white / len(pixels)

    def test_an_unreadable_file_is_kept(self):
        """Guessing here would drop a photo for the wrong reason; the renderer
        already skips files it cannot open."""
        path = os.path.join(self.dir, "broken.jpg")
        with open(path, "w") as handle:
            handle.write("<html>404</html>")
        self.assertFalse(self.looks_like_a_drawing(path))

    def test_drawings_are_dropped_from_a_set(self):
        from social.utils import drop_drawings

        photos = [self.photograph("a.jpg"), self.photograph("b.jpg")]
        paths = [photos[0], self.drawing(), photos[1]]
        self.assertEqual(drop_drawings(paths), photos)

    def test_a_listing_of_nothing_but_drawings_is_still_posted(self):
        """A diagram is a worse post. No post is not a better one."""
        from social.utils import drop_drawings

        paths = [self.drawing("a.jpg"), self.drawing("b.jpg")]
        self.assertEqual(drop_drawings(paths), paths)


class QueueOrderTests(TestCase):
    """Which house goes out next, among the listings that are eligible.

    Ordering only. The featured flag is a filter now (POST_ONLY_FEATURED), so
    these patch it off: what they cover is how eligible listings are ranked
    against each other, which is the same logic either way.

    Featured and cheap are the priority and they outrank posting history. The
    cooldown is what keeps that from collapsing into the old bug, where
    `featured` was the first key and one featured 1400man listing was the head
    of the queue on every run — after it had already been posted — ahead of
    1,558 never-posted properties.
    """

    def property(self, price, featured=False, url=None, posted=None):
        property = Property.objects.create(
            url=url or f"https://example.com/{price}-{featured}",
            price=price, show_in_front=True, featured=featured,
            location="Oita Prefecture",
        )
        PropertyImage.objects.create(property=property, file="properties/a.jpg")
        if posted is not None:
            post = SocialPost.objects.create(
                social_media="instagram", property_url=property.url, caption="",
            )
            # datetime is auto_now_add, so it ignores anything passed to
            # create() and has to be written afterwards. Passing it and not
            # checking is how this helper silently gave every post the same
            # timestamp and made the rotation look like it worked.
            SocialPost.objects.filter(pk=post.pk).update(datetime=posted)
        return property

    def queue(self, limit=None):
        from social import queue as queue_module
        from social.utils import select_properties_to_post

        # Patched rather than overridden: the module reads the constant at
        # import, so override_settings would not reach it. start/addCleanup
        # rather than enterContext, which is 3.11+ and the server is on 3.9.
        patcher = patch.object(queue_module, "POST_ONLY_FEATURED", False)
        patcher.start()
        self.addCleanup(patcher.stop)
        return select_properties_to_post(
            SocialPost.objects.filter(social_media="instagram"),
            price_limit=PRICE_LIMIT_INSTAGRAM, limit=limit,
        )

    def test_the_cheapest_house_goes_first(self):
        expensive = self.property(1400)
        cheap = self.property(200)
        middling = self.property(700)
        self.assertEqual(
            [p.pk for p in self.queue()],
            [cheap.pk, middling.pk, expensive.pk],
        )

    def test_a_featured_house_leads_whatever_it_costs(self):
        """With the filter off, featured is still an ordering preference, and
        it outranks price."""
        featured = self.property(1400, featured=True)
        cheap = self.property(200)
        self.assertEqual([p.pk for p in self.queue()], [featured.pk, cheap.pk])

    def test_a_posted_cheap_house_still_leads_once_it_is_off_cooldown(self):
        """The point of the change: posting history no longer sends a cheap
        listing to the back of the queue."""
        posted = self.property(
            200, posted=timezone.now() - timedelta(days=REPOST_COOLDOWN_DAYS + 1)
        )
        never_posted = self.property(4000)
        self.assertEqual(
            [p.pk for p in self.queue()], [posted.pk, never_posted.pk],
            "the cheaper listing leads even though it has been posted before",
        )

    def test_a_posted_featured_house_still_leads_once_it_is_off_cooldown(self):
        featured = self.property(
            1400, featured=True,
            posted=timezone.now() - timedelta(days=REPOST_COOLDOWN_DAYS + 1),
        )
        never_posted = self.property(200)
        self.assertEqual([p.pk for p in self.queue()], [featured.pk, never_posted.pk])

    def test_a_house_posted_this_week_waits(self):
        """Being the cheapest cannot mean going out twice in a week."""
        just_posted = self.property(200, posted=timezone.now() - timedelta(days=1))
        dearer = self.property(4000)
        self.assertEqual(
            [p.pk for p in self.queue()], [dearer.pk, just_posted.pk],
            "the cooldown outranks both price and featured",
        )

    def test_the_cooldown_outranks_featured_too(self):
        featured = self.property(
            200, featured=True, posted=timezone.now() - timedelta(days=1)
        )
        plain = self.property(4000)
        self.assertEqual([p.pk for p in self.queue()], [plain.pk, featured.pk])

    def test_when_everything_is_cooling_the_longest_wait_leads(self):
        """A small shortlist has every listing on cooldown at once. Falling
        back to price there would repost the cheapest one every run, which is
        what the cooldown exists to prevent."""
        cheap_recent = self.property(200, posted=timezone.now() - timedelta(days=1))
        dear_older = self.property(4000, posted=timezone.now() - timedelta(days=5))
        self.assertEqual(
            [p.pk for p in self.queue()], [dear_older.pk, cheap_recent.pk],
            "the longest-waiting leads, even though the other is cheaper",
        )

    def test_a_never_posted_house_sorts_against_posted_ones_on_merit(self):
        """Never-posted and posted-long-ago share a group now, ranked by
        timestamp — never posted is 0.0, which is to say the longest wait of
        all. A timestamp rather than the datetime because comparing a datetime
        against 0 raises, and these two are now in the same group."""
        never = self.property(4000)
        self.property(
            4000, url="https://example.com/long-ago",
            posted=timezone.now() - timedelta(days=90),
        )
        self.assertEqual(
            self.queue()[0].pk, never.pk,
            "same price, so the one that has waited longest leads",
        )

    def test_a_house_over_the_price_limit_is_not_eligible_at_all(self):
        self.property(PRICE_LIMIT_INSTAGRAM + 1)
        cheap = self.property(200)
        self.assertEqual([p.pk for p in self.queue()], [cheap.pk])

    def test_a_house_with_no_photo_is_not_eligible(self):
        """There is nothing to build a card or a reel out of."""
        Property.objects.create(
            url="https://example.com/photoless", price=100, show_in_front=True,
            location="Oita Prefecture",
        )
        cheap = self.property(200)
        self.assertEqual([p.pk for p in self.queue()], [cheap.pk])

    def test_the_limit_is_respected(self):
        for price in (200, 300, 400):
            self.property(price)
        self.assertEqual(len(self.queue(limit=2)), 2)


class FeaturedFilterTests(TestCase):
    """POST_ONLY_FEATURED: the flag is the shortlist.

    Nothing goes out on social unless somebody marked it in the admin. The size
    of that shortlist is the thing to watch — the queue rotates through what is
    flagged and nothing else.
    """

    def listing(self, url, featured=False, price=200):
        listing = Property.objects.create(
            url=url, price=price, show_in_front=True, featured=featured,
            location="Oita Prefecture",
        )
        PropertyImage.objects.create(property=listing, file="properties/a.jpg")
        return listing

    def queue(self, limit=None, only_featured=True):
        from social import queue as queue_module
        from social.utils import select_properties_to_post

        with patch.object(queue_module, "POST_ONLY_FEATURED", only_featured):
            return select_properties_to_post(
                SocialPost.objects.filter(social_media="instagram"),
                price_limit=PRICE_LIMIT_INSTAGRAM, limit=limit,
            )

    def test_only_featured_listings_are_posted(self):
        featured = self.listing("https://example.com/1", featured=True)
        self.listing("https://example.com/2")
        self.listing("https://example.com/3")
        self.assertEqual([p.pk for p in self.queue()], [featured.pk])

    def test_nothing_featured_means_nothing_to_post(self):
        """Rather than quietly falling back to the whole table."""
        self.listing("https://example.com/1")
        self.assertEqual(self.queue(), [])

    def test_a_featured_listing_still_has_to_be_eligible(self):
        """Flagging a listing does not override the other requirements."""
        self.listing("https://example.com/hidden", featured=True).__class__ \
            .objects.filter(url="https://example.com/hidden").update(show_in_front=False)
        self.assertEqual(self.queue(), [])

    def test_the_shortlist_being_too_small_is_logged(self):
        """One flagged listing is not a rotation, it is the same post again."""
        self.listing("https://example.com/1", featured=True)
        with self.assertLogs("social.queue", level="WARNING") as logs:
            self.queue(limit=2)
        self.assertIn("featured listing(s) are eligible", logs.output[0])
        self.assertIn("admin", logs.output[0])

    def test_a_full_shortlist_is_not_logged_about(self):
        for i in range(4):
            self.listing(f"https://example.com/{i}", featured=True, price=200 + i)
        import logging
        with patch.object(logging.getLogger("social.queue"), "warning") as warn:
            self.assertEqual(len(self.queue(limit=2)), 2)
        warn.assert_not_called()

    def test_turning_the_filter_off_restores_the_whole_pool(self):
        featured = self.listing("https://example.com/1", featured=True, price=100)
        dearer = self.listing("https://example.com/2", price=900)
        self.assertEqual(
            [p.pk for p in self.queue(only_featured=False)],
            [featured.pk, dearer.pk],
            "featured leads on the flag, not on price, and the rest follow",
        )


class ListingCardTests(TestCase):
    """The cards a listing carousel is made of.

    What is worth asserting is the count and the fallbacks: the page dots are
    drawn onto each card from a total worked out up front, so a photo that turns
    out not to be an image has to be dropped before the first card is saved, not
    after. The layout itself is checked by eye — see the preview command.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def photo(self, name, size=(600, 450)):
        """A stand-in for a scraped listing photo, at the size homes.jp serves."""
        from PIL import Image, ImageDraw

        path = os.path.join(self.dir, name)
        canvas = Image.new("RGB", size, (238, 240, 235))
        ImageDraw.Draw(canvas).rectangle(
            [size[0] // 6, size[1] // 3, size[0] * 5 // 6, size[1]],
            fill=(140, 120, 96),
        )
        canvas.save(path, "JPEG")
        return path

    def render(self, photos, **kwargs):
        from social.content.listing_cards import render_listing_cards

        defaults = dict(
            price="US$25,000",
            location="Bungo-ono City, Oita Prefecture",
            building_area="78.5 m²",
            land_area="198.73 m² (60.11 tsubo)",
            link="www.akiyainjapan.com/japanese-houses/1/",
            out_dir=os.path.join(self.dir, "cards"),
            slug="listing-1",
        )
        defaults.update(kwargs)
        return render_listing_cards(photos, **defaults)

    def test_every_photo_gets_a_card_and_the_carousel_closes_on_the_summary(self):
        from PIL import Image

        paths = self.render([self.photo("a.jpg"), self.photo("b.jpg")])

        self.assertEqual(len(paths), 3, "two photos plus the closing card")
        for path in paths:
            self.assertEqual(Image.open(path).size, (1080, 1350),
                             "4:5 is the tallest Instagram shows uncropped")

    def test_a_portrait_photo_is_rendered_too(self):
        """Cover-cropping a portrait photo fills the card; it must not crash."""
        paths = self.render([self.photo("tall.jpg", size=(450, 600))])
        self.assertEqual(len(paths), 2)

    def test_a_file_that_is_not_an_image_is_dropped_before_anything_is_drawn(self):
        """Otherwise the page dots promise a slide that was never rendered."""
        broken = os.path.join(self.dir, "broken.jpg")
        with open(broken, "w") as f:
            f.write("<html>404 Not Found</html>")

        paths = self.render([self.photo("a.jpg"), broken])
        self.assertEqual(len(paths), 2, "one photo plus the closing card")

    def test_nothing_is_rendered_when_no_photo_survives(self):
        """A delisted listing must produce no cards rather than a lone CTA."""
        self.assertEqual(self.render([]), [])

    def test_the_summary_card_can_be_turned_off(self):
        paths = self.render([self.photo("a.jpg")], add_summary=False)
        self.assertEqual(len(paths), 1)

    def test_the_areas_are_shortened_for_the_card(self):
        """A card has no room for two units and false decimals."""
        from social.content.listing_cards import _details_line

        line = _details_line("198.73 m² (60.11 tsubo)", "78.42 m²")
        self.assertIn("Building 199 m²", line)
        self.assertIn("Land 78 m²", line)
        self.assertNotIn("tsubo", line)

    def test_a_thousands_separator_survives(self):
        """'1,074 m²' read as digits-and-dots stops at the comma: 'Land 1 m²'."""
        from social.content.listing_cards import _details_line

        self.assertEqual(_details_line("", "1,074 m²"), "Land 1,074 m²")

    def test_a_missing_area_leaves_no_dangling_separator(self):
        from social.content.listing_cards import _details_line

        self.assertEqual(_details_line("", "78.42 m²"), "Land 78 m²")
        self.assertEqual(_details_line("", ""), "")


    def test_only_our_own_old_cards_are_pruned(self):
        """The text-card pipeline shares the directory, and drafts link to it."""
        from social.content.listing_cards import prune_old_cards

        old_day = time.time() - 60 * 86400
        kept_new = os.path.join(self.dir, "listing-1-20260101-1.jpg")
        pruned = os.path.join(self.dir, "listing-1-20250101-1.jpg")
        other = os.path.join(self.dir, "faq-tax-single-20250101.jpg")
        for path in (kept_new, pruned, other):
            open(path, "w").close()
        for path in (pruned, other):
            os.utime(path, (old_day, old_day))

        self.assertEqual(prune_old_cards(self.dir, "listing-", 30), 1)
        self.assertTrue(os.path.exists(kept_new))
        self.assertTrue(os.path.exists(other), "not ours to delete")
        self.assertFalse(os.path.exists(pruned))

    def test_pruning_can_be_turned_off(self):
        from social.content.listing_cards import prune_old_cards

        old_day = time.time() - 900 * 86400
        path = os.path.join(self.dir, "listing-1-1.jpg")
        open(path, "w").close()
        os.utime(path, (old_day, old_day))

        self.assertEqual(prune_old_cards(self.dir, "listing-", 0), 0)
        self.assertTrue(os.path.exists(path))


class CardLocationTests(TestCase):
    """What place name goes on the image.

    Montserrat has no CJK glyphs, so a Japanese address renders as a row of
    empty boxes — which looks broken in a way that saying nothing does not.
    """

    def location(self, raw):
        from social.utils import _card_location

        return _card_location(
            Property(url="https://example.com/1", price=1800, location=raw)
        )

    def test_a_latin_address_is_used_as_it_is(self):
        self.assertEqual(
            self.location("Bungo-ono City, Oita Prefecture"),
            "Bungo-ono City, Oita Prefecture",
        )

    def test_the_scraper_junk_is_stripped(self):
        self.assertEqual(
            self.location("Oita Prefecture [ ■ Surrounding environment ]"),
            "Oita Prefecture",
        )

    def test_a_mixed_address_keeps_the_part_that_will_render(self):
        self.assertEqual(self.location("大分県 Bungo-ono City"), "Bungo-ono City")

    def test_an_address_with_nothing_renderable_yields_nothing(self):
        """Better a card with no place on it than one with three empty boxes."""
        self.assertEqual(self.location("大分県豊後大野市"), "")


class ListingMediaTests(TestCase):
    """What a listing post actually uploads.

    The branded cards are the point of the format, but they depend on a render
    and on our own domain serving the result. Both run from cron with nobody
    watching, so the fallback to the raw photos matters as much as the cards:
    an unbranded post beats no post.
    """

    def setUp(self):
        self.property = Property.objects.create(
            url="https://example.com/house-1", price=1800, show_in_front=True,
            location="Oita Prefecture, Bungo-ono City",
            building_area="78.5m 2", land_area="198.73m 2",
        )
        # Photos are stored as the source listing's own URL, which is what
        # prepare_image_url_for_facebook exists to dig back out of MEDIA_URL.
        PropertyImage.objects.create(
            property=self.property, file="https://img.example.com/a.jpg"
        )

    def post(self, card_urls):
        """Post to Instagram with the card pipeline stubbed to `card_urls`."""
        from social import utils

        self.uploaded = []

        def fake_post(url, data=None, **kwargs):
            if url.endswith("/media") and data.get("is_carousel_item"):
                self.uploaded.append(data["image_url"])
                return response(200, {"id": f"child-{len(self.uploaded)}"})
            if url.endswith("/media"):
                return response(200, {"id": "carousel-1"})
            return response(200, {"id": "17999999999999999"})

        with patch.object(utils, "_listing_card_urls", return_value=card_urls), \
             patch.object(utils, "get_fresh_token", return_value="token"), \
             patch.object(utils.requests, "post", side_effect=fake_post):
            utils.post_to_instagram(
                property=self.property, last_caption_generated="",
                use_ai_caption=False,
            )

    def test_the_branded_cards_are_what_gets_uploaded(self):
        self.post(["https://www.akiyainjapan.com/static/social_cards/a-1.jpg"])
        self.assertEqual(
            self.uploaded,
            ["https://www.akiyainjapan.com/static/social_cards/a-1.jpg"],
        )

    def test_a_card_url_is_uploaded_untouched(self):
        """The raw-photo URL fixups would mangle one of our own URLs."""
        self.post(["https://www.akiyainjapan.com/static/social_cards/a-1.jpg"])
        for url in self.uploaded:
            self.assertNotIn("https:///", url)

    def test_a_failed_render_falls_back_to_the_raw_photos(self):
        self.post(None)
        self.assertEqual(self.uploaded, ["https://img.example.com/a.jpg"])
        self.assertEqual(SocialPost.objects.count(), 1,
                         "an unbranded post still has to go out")


@unittest.skipUnless(
    os.environ.get("RENDER_REEL"),
    "slow: encodes a real video. Run with RENDER_REEL=1 to check the overlay "
    "layout, then look at the frame it writes to RENDER_REEL_DIR (default /tmp).",
)
class ReelRenderSmokeTests(TestCase):
    """Actually encode a reel and keep the first frame to look at.

    Off by default because it runs ffmpeg. It exists because the overlay layout
    cannot be verified by assertion: moviepy raises when text overflows its
    caption box, and the failure path swallows that and posts the video with no
    overlays at all — so a too-long price or place name would silently ship
    unbranded reels rather than break a test.
    """

    def test_the_hook_frame_renders(self):
        from moviepy import VideoFileClip
        from PIL import Image, ImageDraw

        from social import utils

        out_dir = os.environ.get("RENDER_REEL_DIR", "/tmp")
        photo = os.path.join(out_dir, "reel-source.jpg")
        canvas = Image.new("RGB", (1200, 900), (90, 120, 90))
        ImageDraw.Draw(canvas).rectangle([200, 300, 1000, 800], fill=(150, 130, 100))
        canvas.save(photo, "JPEG")

        property = Property.objects.create(
            url="https://example.com/render", price=1800, show_in_front=True,
            featured=True,
            location="Oita Prefecture, Bungo-ono City, Mitsuke", floor_plan="3DK",
            # Areas, so the render exercises the brass size line as well.
            building_area="118.12㎡", land_area="1,074㎡ (public book)",
        )
        PropertyImage.objects.create(property=property, file="properties/a.jpg")

        audio = os.path.join(
            "static", "audios_for_social_posts", "zen-garden-310599.mp3"
        )
        video_path = os.path.join(out_dir, "reel-render.mp4")
        meta = {}
        with patch.object(utils, "_download_image_to_tempfile", return_value=photo), \
             patch.object(utils, "ai_client") as ai, \
             self.assertLogs("social.utils", level="INFO") as logs:
            ai.return_value.generate_text.return_value = "Wake Up Here"
            result = utils.create_property_video(
                property.pk, output_path=video_path, audio_path=audio,
                duration_per_image=2, meta=meta,
            )

        self.assertEqual(result, video_path)
        self.assertEqual(meta["overlay_hook"], "Wake Up Here")
        self.assertTrue(meta["hook_price_first"])
        self.assertTrue(os.path.getsize(video_path) > 0)

        # The one assertion this test exists for. An overflowing caption box
        # raises, the composite is abandoned, and the no-label video is posted
        # instead — same return value, same non-zero file, no overlays on it.
        # Without this the test passes while the reel goes out unbranded.
        fell_back = [line for line in logs.output if "no-label" in line]
        self.assertEqual(fell_back, [], "the overlays did not composite")

        # And keep a frame to look at, which is what the docstring promises.
        frame_path = os.path.join(out_dir, "reel-frame.png")
        VideoFileClip(video_path).save_frame(frame_path, t=1)
        self.assertTrue(os.path.getsize(frame_path) > 0)
