"""Tests for the TikTok poster.

All mocked — no app registration existed when this was written, so nothing here
has spoken to TikTok. That is worth saying plainly: these tests describe what
the docs say the API does, and they will catch a regression in our half of it,
but the first real post is still the first real test.

What they do cover is the three things this integration can get wrong silently:
a 200 response carrying an error inside it, a rotated refresh token being
dropped, and an off-by-one in the upload's byte range.
"""

import json
import os
import shutil
import tempfile
import time
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from inventory.models import Property, PropertyImage
from social import tiktok
from social.models import SocialPost


def ok(data=None):
    """A successful TikTok response: error code 'ok', payload under data."""
    return http(200, {"data": data or {}, "error": {"code": "ok", "message": ""}})


def http(status, payload):
    class _Response:
        status_code = status
        content = b"x"
        text = json.dumps(payload)

        def json(self):
            return payload

    return _Response()


class TokenStoreTests(SimpleTestCase):
    """The refresh token is the only thing between cron and a browser."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "tiktok_token.json")
        self.override = override_settings(
            TIKTOK_TOKEN_FILE=self.path,
            TIKTOK_CLIENT_KEY="key", TIKTOK_CLIENT_SECRET="secret",
        )
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_rotated_refresh_token_replaces_the_old_one(self):
        """TikTok may hand back a new one, and dropping it locks us out."""
        tiktok.save_tokens({"access_token": "old", "refresh_token": "first"})
        with patch.object(tiktok.requests, "post", return_value=http(200, {
            "access_token": "new", "refresh_token": "second", "expires_in": 86400,
        })):
            tiktok.refresh_access_token()

        self.assertEqual(tiktok.load_tokens()["refresh_token"], "second")

    def test_an_unrotated_refresh_token_survives_the_refresh(self):
        tiktok.save_tokens({"access_token": "old", "refresh_token": "first"})
        with patch.object(tiktok.requests, "post", return_value=http(200, {
            "access_token": "new", "refresh_token": "first", "expires_in": 86400,
        })):
            tiktok.refresh_access_token()

        self.assertEqual(tiktok.load_tokens()["refresh_token"], "first")

    def test_a_lifetime_is_stored_as_a_deadline(self):
        """expires_in is only meaningful at the moment it is received."""
        tiktok.save_tokens({"refresh_token": "first"})
        with patch.object(tiktok.requests, "post", return_value=http(200, {
            "access_token": "new", "refresh_token": "first", "expires_in": 86400,
        })):
            tiktok.refresh_access_token()

        self.assertGreater(tiktok.load_tokens()["expires_at"], time.time() + 86000)

    def test_an_unexpired_token_is_reused(self):
        tiktok.save_tokens({
            "access_token": "still-good", "refresh_token": "r",
            "expires_at": time.time() + 3600,
        })
        with patch.object(tiktok.requests, "post") as post:
            self.assertEqual(tiktok.get_fresh_token(), "still-good")
        post.assert_not_called()

    def test_an_expired_token_is_refreshed(self):
        tiktok.save_tokens({
            "access_token": "stale", "refresh_token": "r",
            "expires_at": time.time() - 1,
        })
        with patch.object(tiktok.requests, "post", return_value=http(200, {
            "access_token": "fresh", "refresh_token": "r", "expires_in": 86400,
        })):
            self.assertEqual(tiktok.get_fresh_token(), "fresh")

    def test_no_stored_token_says_what_to_run(self):
        with self.assertRaises(tiktok.TikTokError) as caught:
            tiktok.refresh_access_token()
        self.assertIn("tiktok_auth", str(caught.exception))

    def test_a_failed_refresh_does_not_destroy_the_stored_token(self):
        """Otherwise one bad night costs the year-long refresh token."""
        tiktok.save_tokens({"access_token": "old", "refresh_token": "precious"})
        with patch.object(tiktok.requests, "post",
                          return_value=http(400, {"error": "invalid_grant"})):
            with self.assertRaises(tiktok.TikTokError):
                tiktok.refresh_access_token()

        self.assertEqual(tiktok.load_tokens()["refresh_token"], "precious")


@override_settings(TIKTOK_CLIENT_KEY="key", TIKTOK_CLIENT_SECRET="secret")
class PublishTests(SimpleTestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.video = os.path.join(self.dir, "reel.mp4")
        with open(self.video, "wb") as handle:
            handle.write(b"\0" * 2048)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def publish(self, creator=None, caption="A house", put_status=200):
        creator = creator if creator is not None else {
            "creator_username": "akiyainjapan",
            "privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
        }
        self.posts = []

        def fake_post(url, **kwargs):
            self.posts.append((url, kwargs))
            if url.endswith("/creator_info/query/"):
                return ok(creator)
            return ok({"publish_id": "pub-1", "upload_url": "https://upload/xyz"})

        with patch.object(tiktok, "get_fresh_token", return_value="token"), \
             patch.object(tiktok.requests, "post", side_effect=fake_post), \
             patch.object(tiktok.requests, "put",
                          return_value=http(put_status, {})) as put:
            result = tiktok.publish_video(self.video, caption)
        self.put = put
        return result

    def init_body(self):
        return next(kwargs["json"] for url, kwargs in self.posts
                    if url.endswith("/video/init/"))

    def test_the_publish_id_comes_back(self):
        self.assertEqual(self.publish(), "pub-1")

    def test_the_whole_file_goes_up_as_one_chunk(self):
        self.publish()
        source = self.init_body()["source_info"]
        self.assertEqual(source["source"], "FILE_UPLOAD")
        self.assertEqual(source["video_size"], 2048)
        self.assertEqual(source["chunk_size"], 2048)
        self.assertEqual(source["total_chunk_count"], 1)

    def test_the_byte_range_is_inclusive(self):
        """bytes 0-2047/2048. Off by one here is accepted and fails later."""
        self.publish()
        headers = self.put.call_args.kwargs["headers"]
        self.assertEqual(headers["Content-Range"], "bytes 0-2047/2048")
        self.assertEqual(headers["Content-Length"], "2048")

    def test_the_creator_is_asked_before_the_post_is_made(self):
        """Required by TikTok, and the only source of the allowed privacy levels."""
        self.publish()
        self.assertTrue(self.posts[0][0].endswith("/creator_info/query/"))

    @override_settings()
    def test_a_privacy_level_the_account_cannot_use_falls_back_to_private(self):
        with patch.object(tiktok, "TIKTOK_PRIVACY_LEVEL", "PUBLIC_TO_EVERYONE"):
            self.publish(creator={"privacy_level_options": ["SELF_ONLY"]})
        self.assertEqual(self.init_body()["post_info"]["privacy_level"], "SELF_ONLY")

    def test_an_account_with_comments_off_is_posted_to_with_comments_off(self):
        """Sending the opposite is a rejected post, not a negotiation."""
        self.publish(creator={
            "privacy_level_options": ["SELF_ONLY"], "comment_disabled": True,
        })
        self.assertTrue(self.init_body()["post_info"]["disable_comment"])

    def test_a_long_caption_is_cut_to_the_title_limit(self):
        self.publish(caption="x" * 3000)
        self.assertEqual(len(self.init_body()["post_info"]["title"]), 2200)

    def test_an_error_inside_a_200_is_still_an_error(self):
        """The trap: TikTok answers 200 and puts the refusal in the body."""
        refusal = http(200, {"error": {
            "code": "unaudited_client_can_only_post_to_private_accounts",
            "message": "app not audited",
        }})
        with patch.object(tiktok, "get_fresh_token", return_value="token"), \
             patch.object(tiktok.requests, "post", return_value=refusal):
            with self.assertRaises(tiktok.TikTokError) as caught:
                tiktok.publish_video(self.video, "caption")

        self.assertEqual(
            caught.exception.code,
            "unaudited_client_can_only_post_to_private_accounts",
            "the code is the half worth reading",
        )

    def test_a_failed_upload_raises(self):
        with self.assertRaises(tiktok.TikTokError):
            self.publish(put_status=500)

    def test_an_empty_video_is_refused_before_anything_is_sent(self):
        open(self.video, "wb").close()
        with self.assertRaises(tiktok.TikTokError):
            self.publish()

    def test_a_video_too_big_for_one_chunk_is_refused_rather_than_truncated(self):
        with patch.object(tiktok.os.path, "getsize",
                          return_value=tiktok.MAX_SINGLE_CHUNK + 1):
            with self.assertRaises(tiktok.TikTokError) as caught:
                self.publish()
        self.assertIn("chunked upload is not implemented", str(caught.exception))


class PostTikTokReelTests(TestCase):
    """The run itself: what gets recorded, and what happens when TikTok says no."""

    def setUp(self):
        self.property = Property.objects.create(
            url="https://example.com/house-1", price=200, show_in_front=True,
            location="Oita Prefecture, Bungo-ono City",
            building_area="78.5m 2", land_area="198.73m 2",
        )
        PropertyImage.objects.create(
            property=self.property, file="https://img.example.com/a.jpg"
        )

    def run_post(self, publish=None):
        from social import utils

        def fake_video(property_id, output_path, audio_path,
                       duration_per_image=3, meta=None):
            with open(output_path, "wb") as handle:
                handle.write(b"\0" * 16)
            if meta is not None:
                meta["overlay_hook"] = "Wake Up Here"
            return output_path

        with patch.object(utils, "create_property_video", side_effect=fake_video), \
             patch.object(utils, "_get_random_mp3_full_path",
                          return_value="/audios/zen-garden-310599.mp3"), \
             patch.object(utils, "generate_caption_for_post",
                          return_value=("ai", "US$14,000 · Oita", "angle")), \
             patch.object(utils.time, "sleep"), \
             patch.object(tiktok, "publish_video",
                          **(publish or {"return_value": "pub-1"})), \
             patch.object(tiktok, "fetch_status",
                          return_value={"status": "PUBLISH_COMPLETE"}):
            return utils.post_tiktok_reel()

    def test_the_post_is_recorded_against_tiktok(self):
        self.assertTrue(self.run_post())
        post = SocialPost.objects.get()
        self.assertEqual(post.social_media, "tiktok")
        self.assertEqual(post.content_type, "reel")
        self.assertEqual(post.media_id, "pub-1")
        self.assertEqual(post.overlay_hook, "Wake Up Here")

    def test_a_refusal_records_nothing_and_does_not_raise(self):
        """It runs from cron next to the other posters; it may not take them down."""
        refused = {"side_effect": tiktok.TikTokError("nope", code="spam_risk")}
        self.assertFalse(self.run_post(publish=refused))
        self.assertEqual(SocialPost.objects.count(), 0)

    def test_the_video_is_cleaned_up_either_way(self):
        self.run_post(publish={"side_effect": tiktok.TikTokError("nope")})
        self.assertFalse(os.path.exists("property_video_tiktok.mp4"),
                         "a video per run fills a small disk")

    def test_nothing_to_post_is_not_an_error(self):
        Property.objects.all().update(show_in_front=False)
        self.assertFalse(self.run_post())
