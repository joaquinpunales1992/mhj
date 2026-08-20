"""Tests for the parts of the social bot that decide reach.

Nothing here talks to Meta or encodes video. What is worth covering is the
bookkeeping that makes the bot answerable — the published id being stored, the
insights landing on the right row, and averages that exclude what was never
measured — plus the two caption/hook rules that changed, because a silent
regression there costs reach without breaking anything.
"""

import json
import os
import unittest
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from inventory.models import Property, PropertyImage
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
        self.assertIn("🏡 Building: 78.5m²", caption)
        self.assertIn("🌳 Land: 198.73m² (60.11 tsubo)", caption)
        self.assertIn("www.akiyainjapan.com/japanese-houses/1/", caption)
        self.assertIn("#akiya", caption)
        self.assertIn("#oita", caption, "location hashtag should be derived")

    def test_the_angle_comes_back_so_it_can_be_stored(self):
        with patch("social.utils.CerebrasAI") as ai:
            ai.return_value.generate_text.return_value = "A hook\n\nBody copy."
            ai_caption, caption, angle = self.caption(use_ai_caption=True)
        self.assertTrue(angle, "the creative direction used must be reported")
        self.assertIn("A hook", ai_caption)
        self.assertTrue(caption.startswith("US$18,000 · Oita Prefecture"))

    def test_a_dead_ai_still_produces_a_price_led_caption(self):
        with patch("social.utils.CerebrasAI") as ai:
            ai.return_value.generate_text.side_effect = RuntimeError("no service")
            _, caption, angle = self.caption(use_ai_caption=True)
        self.assertTrue(caption.startswith("US$18,000 · Oita Prefecture"))
        self.assertEqual(angle, "", "no angle was used, so none may be recorded")

    def test_a_missing_location_does_not_leave_a_dangling_separator(self):
        _, caption, _ = self.caption(location="")
        self.assertTrue(caption.startswith("US$18,000\n"))


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
        self.property = Property.objects.create(
            url="https://example.com/house-1", price=1800, show_in_front=True,
            location="Oita Prefecture, Bungo-ono City", floor_plan="3DK",
            building_area="78.5m 2", land_area="198.73m 2",
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
             patch.object(utils, "CerebrasAI") as ai, \
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
             patch.object(social_bot, "CerebrasAI") as ai:
            ai.return_value.generate_text.return_value = "It depends on the town — ask us!"
            social_bot.reply_comments_instagram()

        prompt = ai.return_value.generate_text.call_args.kwargs["prompt"]
        self.assertIn("How much is the property tax?", prompt)
        reply.assert_called_once()

        comment = SocialComment.objects.get()
        self.assertTrue(comment.replied)
        self.assertEqual(comment.comment, "It depends on the town — ask us!",
                         "the reply text belongs here, not the API's response object")


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
        from PIL import Image, ImageDraw

        from social import utils

        out_dir = os.environ.get("RENDER_REEL_DIR", "/tmp")
        photo = os.path.join(out_dir, "reel-source.jpg")
        canvas = Image.new("RGB", (1200, 900), (90, 120, 90))
        ImageDraw.Draw(canvas).rectangle([200, 300, 1000, 800], fill=(150, 130, 100))
        canvas.save(photo, "JPEG")

        property = Property.objects.create(
            url="https://example.com/render", price=1800, show_in_front=True,
            location="Oita Prefecture, Bungo-ono City, Mitsuke", floor_plan="3DK",
        )
        PropertyImage.objects.create(property=property, file="properties/a.jpg")

        audio = os.path.join(
            "static", "audios_for_social_posts", "zen-garden-310599.mp3"
        )
        video_path = os.path.join(out_dir, "reel-render.mp4")
        meta = {}
        with patch.object(utils, "_download_image_to_tempfile", return_value=photo), \
             patch.object(utils, "CerebrasAI") as ai:
            ai.return_value.generate_text.return_value = "Wake Up Here"
            result = utils.create_property_video(
                property.pk, output_path=video_path, audio_path=audio,
                duration_per_image=2, meta=meta,
            )

        self.assertEqual(result, video_path)
        self.assertEqual(meta["overlay_hook"], "Wake Up Here")
        self.assertTrue(meta["hook_price_first"])
        self.assertTrue(os.path.getsize(video_path) > 0)
