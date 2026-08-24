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

    def test_tokens_from_the_other_app_are_refused_by_name(self):
        """Sandbox and production are separate apps with separate keys.

        Swapping the credentials without reconnecting otherwise fails at refresh
        time with an error from TikTok that says nothing about the cause.
        """
        tiktok.save_tokens({
            "access_token": "a", "refresh_token": "r", "client_key": "sandboxkey",
        })
        with patch.object(tiktok.requests, "post") as post:
            with self.assertRaises(tiktok.TikTokError) as caught:
                tiktok.refresh_access_token()

        post.assert_not_called()
        self.assertIn("sandboxkey", str(caught.exception))
        self.assertIn("Reconnect", str(caught.exception))

    def test_a_refresh_stamps_the_app_that_issued_the_tokens(self):
        tiktok.save_tokens({"refresh_token": "r"})
        with patch.object(tiktok.requests, "post", return_value=http(200, {
            "access_token": "new", "refresh_token": "r", "expires_in": 86400,
        })):
            tiktok.refresh_access_token()
        self.assertEqual(tiktok.load_tokens()["client_key"], "key")

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


class TikTokPageTests(TestCase):
    """The staff page that TikTok's Direct Post rules and its app review need.

    Two things are worth protecting here. It must not be reachable by visitors —
    it posts as the site's own account. And the choices made on it must actually
    reach the API, because a page that shows a privacy selector and then posts
    whatever the constant says is worse than no page: it tells the operator
    something untrue.
    """

    CREATOR = {
        "creator_username": "akiyainjapan",
        "creator_nickname": "My Akiya in Japan",
        "privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
    }
    PROFILE = {"open_id": "open-1", "display_name": "My Akiya in Japan",
               "avatar_url_100": "https://example.com/a.jpg"}

    def setUp(self):
        from django.contrib.auth.models import User

        self.staff = User.objects.create_user(
            "staff", "staff@example.com", "pw", is_staff=True
        )
        self.visitor = User.objects.create_user("v", "v@example.com", "pw")
        self.property = Property.objects.create(
            url="https://example.com/house-1", price=200, show_in_front=True,
            location="Oita Prefecture, Bungo-ono City",
        )
        PropertyImage.objects.create(
            property=self.property, file="https://img.example.com/a.jpg"
        )

    def test_a_visitor_cannot_see_it(self):
        self.client.force_login(self.visitor)
        response = self.client.get("/tiktok/")
        self.assertNotEqual(response.status_code, 200)

    def test_an_anonymous_request_cannot_see_it(self):
        self.assertNotEqual(self.client.get("/tiktok/").status_code, 200)

    def test_a_visitor_cannot_post(self):
        self.client.force_login(self.visitor)
        with patch.object(tiktok, "publish_video") as publish:
            self.client.post("/tiktok/post/", {"privacy_level": "SELF_ONLY"})
        publish.assert_not_called()

    def test_the_page_shows_the_account_and_the_levels_it_allows(self):
        self.client.force_login(self.staff)
        with patch.object(tiktok, "get_fresh_token", return_value="t"), \
             patch.object(tiktok, "fetch_user_info", return_value=self.PROFILE), \
             patch.object(tiktok, "query_creator_info", return_value=self.CREATOR):
            response = self.client.get("/tiktok/")
        self.assertContains(response, "akiyainjapan")
        self.assertContains(response, "PUBLIC_TO_EVERYONE")
        self.assertContains(response, "SELF_ONLY")

    def test_the_page_renders_when_the_profile_lookup_fails(self):
        """The profile is decoration. Not being able to post is the failure.

        This broke the page once: an empty profile dict in a filter chain raises
        rather than falling through to the creator_info values behind it.
        """
        with patch.object(tiktok, "get_fresh_token", return_value="t"), \
             patch.object(tiktok, "fetch_user_info",
                          side_effect=tiktok.TikTokError("no scope")), \
             patch.object(tiktok, "query_creator_info", return_value=self.CREATOR):
            self.client.force_login(self.staff)
            response = self.client.get("/tiktok/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "akiyainjapan")
        self.assertContains(response, "My Akiya in Japan")

    def test_an_unconnected_account_is_offered_the_connect_link(self):
        self.client.force_login(self.staff)
        with patch.object(tiktok, "get_fresh_token",
                          side_effect=tiktok.TikTokError("no token")):
            response = self.client.get("/tiktok/")
        self.assertContains(response, "/tiktok/connect/")

    def test_the_page_survives_tiktok_being_unreachable(self):
        """A dead API is a message on the page, not a 500."""
        self.client.force_login(self.staff)
        with patch.object(tiktok, "get_fresh_token", side_effect=OSError("dns")):
            self.assertEqual(self.client.get("/tiktok/").status_code, 200)

    def test_connect_sends_the_operator_to_tiktok_with_a_state(self):
        self.client.force_login(self.staff)
        with override_settings(TIKTOK_CLIENT_KEY="key", TIKTOK_CLIENT_SECRET="s"):
            response = self.client.get("/tiktok/connect/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("www.tiktok.com", response["Location"])
        # Both scopes: Login Kit is a prerequisite of the posting product and
        # brings user.info.basic, which the page uses rather than merely holds.
        self.assertIn("user.info.basic", response["Location"])
        self.assertIn("video.publish", response["Location"])
        self.assertIn(self.client.session["tiktok_oauth_state"],
                      response["Location"])

    def test_a_callback_with_the_wrong_state_does_not_spend_the_code(self):
        """Otherwise a crafted link could connect an account we did not choose."""
        self.client.force_login(self.staff)
        with patch.object(tiktok, "exchange_code") as exchange:
            self.client.get("/tiktok/callback/?code=abc&state=forged")
        exchange.assert_not_called()

    def test_a_callback_with_the_right_state_stores_the_tokens(self):
        self.client.force_login(self.staff)
        with override_settings(TIKTOK_CLIENT_KEY="key", TIKTOK_CLIENT_SECRET="s"):
            self.client.get("/tiktok/connect/")
        state = self.client.session["tiktok_oauth_state"]
        with patch.object(tiktok, "exchange_code",
                          return_value={"open_id": "o"}) as exchange:
            self.client.get(f"/tiktok/callback/?code=abc&state={state}")
        exchange.assert_called_once()
        self.assertEqual(exchange.call_args.args[0], "abc")

    def post_now(self, data):
        """Post through the page, with the encoder and the API mocked."""
        from social import utils

        def fake_video(property_id, output_path, audio_path,
                       duration_per_image=3, meta=None):
            with open(output_path, "wb") as handle:
                handle.write(b"\0" * 16)
            return output_path

        self.client.force_login(self.staff)
        with patch.object(utils, "create_property_video", side_effect=fake_video), \
             patch.object(utils, "_get_random_mp3_full_path", return_value="/a.mp3"), \
             patch.object(utils, "generate_caption_for_post",
                          return_value=("ai", "US$14,000 · Oita", "angle")), \
             patch.object(utils.time, "sleep"), \
             patch.object(tiktok, "fetch_status", return_value={"status": "OK"}), \
             patch.object(tiktok, "publish_video", return_value="pub-1") as publish:
            self.client.post("/tiktok/post/", data)
        return publish

    def test_the_chosen_privacy_level_reaches_the_api(self):
        publish = self.post_now({"privacy_level": "PUBLIC_TO_EVERYONE"})
        self.assertEqual(
            publish.call_args.kwargs["privacy_level"], "PUBLIC_TO_EVERYONE"
        )

    def test_the_chosen_switches_reach_the_api(self):
        publish = self.post_now({
            "privacy_level": "SELF_ONLY", "disable_comment": "on",
        })
        options = publish.call_args.kwargs["options"]
        self.assertTrue(options["disable_comment"])
        self.assertFalse(options["disable_duet"])
        self.assertFalse(options["disable_stitch"])

    def test_posting_records_the_post(self):
        self.post_now({"privacy_level": "SELF_ONLY"})
        self.assertEqual(SocialPost.objects.filter(social_media="tiktok").count(), 1)
