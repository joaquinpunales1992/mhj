"""Tests for the model layer.

No network. What is worth covering is the two things that will actually go
wrong: the schema translation, because Gemini rejects the JSON Schema our
callers already write, and the fallback order, because the whole point of the
chain is what happens on the bad day rather than the good one.
"""

import json
import os
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from ai import gemini, providers
from ai.gemini import GeminiAI, _to_gemini_schema

CAPTION_SCHEMA = {
    "type": "object",
    "properties": {"caption": {"type": "string"}},
    "required": ["caption"],
    "additionalProperties": False,
}


def http(status=200, payload=None, text=""):
    """A stand-in for a requests response, raise_for_status included.

    Without it every model listing in here fell back to PREFERRED_MODELS and the
    resolution tests passed while testing nothing.
    """

    class _Response:
        status_code = status

        def json(self):
            return payload or {}

        def raise_for_status(self):
            if status >= 400:
                raise RuntimeError(f"HTTP {status}")

    _Response.text = text or json.dumps(payload or {})
    return _Response()


def generated(text):
    """A generateContent response carrying `text`."""
    return http(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})


def model_list(*names):
    return http(200, {"models": [
        {"name": f"models/{name}", "supportedGenerationMethods": ["generateContent"]}
        for name in names
    ]})


class SchemaTranslationTests(SimpleTestCase):
    """responseSchema takes a subset of JSON Schema, not JSON Schema."""

    def test_additional_properties_is_dropped(self):
        """Both of our callers set it, and the API rejects the request for it."""
        self.assertNotIn("additionalProperties", _to_gemini_schema(CAPTION_SCHEMA))

    def test_types_are_upper_cased(self):
        out = _to_gemini_schema(CAPTION_SCHEMA)
        self.assertEqual(out["type"], "OBJECT")
        self.assertEqual(out["properties"]["caption"]["type"], "STRING")

    def test_required_survives(self):
        self.assertEqual(_to_gemini_schema(CAPTION_SCHEMA)["required"], ["caption"])

    def test_a_nested_schema_is_translated_all_the_way_down(self):
        nested = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
            },
        }
        out = _to_gemini_schema(nested)
        item = out["properties"]["items"]["items"]
        self.assertEqual(out["properties"]["items"]["type"], "ARRAY")
        self.assertEqual(item["properties"]["name"]["type"], "STRING")
        self.assertNotIn("additionalProperties", item)


@override_settings(GEMINI_API_KEY="test-key")
class GeminiClientTests(SimpleTestCase):

    def test_a_caption_comes_back_as_a_string(self):
        with patch.object(gemini.requests, "get", return_value=model_list("gemini-2.5-flash")), \
             patch.object(gemini.requests, "post",
                          return_value=generated('{"caption": "A quiet valley"}')):
            self.assertEqual(GeminiAI().generate_text("prompt"), "A quiet valley")

    def test_the_key_travels_in_the_header_not_the_url(self):
        """A key in the query string ends up in logs and proxies."""
        with patch.object(gemini.requests, "get", return_value=model_list("gemini-2.5-flash")), \
             patch.object(gemini.requests, "post",
                          return_value=generated('{"caption": "x"}')) as post:
            GeminiAI().generate_text("prompt")
        self.assertEqual(post.call_args.kwargs["headers"]["x-goog-api-key"], "test-key")
        self.assertNotIn("test-key", post.call_args.args[0])

    def test_a_refused_model_falls_through_to_the_next_one(self):
        with patch.object(gemini.requests, "get",
                          return_value=model_list("gemini-2.5-flash", "gemini-2.0-flash")), \
             patch.object(gemini.requests, "post", side_effect=[
                 http(429, text="quota exhausted"),
                 generated('{"caption": "Second model"}'),
             ]) as post:
            self.assertEqual(GeminiAI().generate_text("p"), "Second model")
        self.assertEqual(post.call_count, 2)

    def test_the_error_body_reaches_the_exception(self):
        """A 400 alone has sent me looking in the wrong place; the body says why."""
        with patch.object(gemini.requests, "get", return_value=model_list("gemini-2.5-flash")), \
             patch.object(gemini.requests, "post",
                          return_value=http(400, text="API key not valid")):
            with self.assertRaises(RuntimeError) as caught:
                GeminiAI().generate_text("p")
        self.assertIn("API key not valid", str(caught.exception))

    def test_an_answer_withheld_says_why(self):
        """200 with no parts: a safety block, or thinking ate the token budget."""
        withheld = http(200, {"candidates": [{"finishReason": "MAX_TOKENS",
                                              "content": {"parts": []}}]})
        with patch.object(gemini.requests, "get", return_value=model_list("gemini-2.5-flash")), \
             patch.object(gemini.requests, "post", return_value=withheld):
            with self.assertRaises(RuntimeError) as caught:
                GeminiAI().generate_text("p")
        self.assertIn("MAX_TOKENS", str(caught.exception))

    def test_only_models_that_can_generate_are_used(self):
        listing = http(200, {"models": [
            {"name": "models/embedding-001",
             "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/gemini-2.0-flash",
             "supportedGenerationMethods": ["generateContent"]},
        ]})
        with patch.object(gemini.requests, "get", return_value=listing):
            self.assertEqual(GeminiAI()._resolve_models(), ["gemini-2.0-flash"])

    def test_the_preferred_model_goes_first(self):
        with patch.object(gemini.requests, "get",
                          return_value=model_list("gemini-9-turbo",
                                                  "gemini-flash-latest")):
            self.assertEqual(
                GeminiAI()._resolve_models(),
                ["gemini-flash-latest", "gemini-9-turbo"],
                "and a model we have never heard of stays on as a fallback",
            )

    def test_a_retired_model_is_not_asked_twice(self):
        """The listing endpoint advertises models that answer 404 when called,
        so this is the only place that finds out — and a retired model does not
        come back. Asking it again on every call is pure latency and pure
        quota: two wasted requests per generation, against a free tier that
        allows twenty a day."""
        GeminiAI._RETIRED.clear()
        self.addCleanup(GeminiAI._RETIRED.clear)

        gone = http(404, text="no longer available to new users")
        with patch.object(gemini.requests, "get",
                          return_value=model_list("gemini-flash-latest")), \
             patch.object(gemini.requests, "post", return_value=gone) as post:
            client = GeminiAI(api_key="k")
            for _ in range(3):
                with self.assertRaises(RuntimeError):
                    client.generate_text("hello")

        self.assertEqual(post.call_count, 1,
                         "asked once, then remembered")
        self.assertIn("gemini-flash-latest", GeminiAI._RETIRED)

    def test_a_speech_or_image_model_is_never_used_for_a_caption(self):
        """A caption was attempted against gemini-2.5-pro-tts.

        Every model Google lists advertises generateContent, including the
        text-to-speech and image ones, so "whatever else the account has" is not
        a safe fallback the way it is on Cerebras's much shorter list.
        """
        listing = model_list(
            "gemini-2.5-pro-tts", "gemini-2.5-flash-image", "imagen-4.0",
            "text-embedding-004", "gemini-2.5-flash", "gemini-3-pro",
        )
        with patch.object(gemini.requests, "get", return_value=listing):
            resolved = GeminiAI()._resolve_models()

        self.assertEqual(resolved[0], "gemini-2.5-flash", "preferred still first")
        self.assertIn("gemini-3-pro", resolved, "an unknown text model is usable")
        for rejected in ("gemini-2.5-pro-tts", "gemini-2.5-flash-image",
                         "imagen-4.0", "text-embedding-004"):
            self.assertNotIn(rejected, resolved)

    def test_a_listing_failure_does_not_stop_generation(self):
        """Whether we can list models is not whether we can use one."""
        with patch.object(gemini.requests, "get", side_effect=OSError("dns")):
            self.assertEqual(GeminiAI()._resolve_models(), GeminiAI.PREFERRED_MODELS)

    def test_the_models_are_listed_once_per_client(self):
        with patch.object(gemini.requests, "get", return_value=model_list("gemini-2.5-flash")) as get, \
             patch.object(gemini.requests, "post",
                          return_value=generated('{"caption": "x"}')):
            client = GeminiAI()
            client.generate_text("one")
            client.generate_text("two")
        self.assertEqual(get.call_count, 1)


class MissingKeyTests(SimpleTestCase):

    @override_settings(GEMINI_API_KEY="")
    def test_no_key_means_the_provider_refuses_to_be_built(self):
        """So the chain skips it, rather than sending an unauthenticated call."""
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            with self.assertRaises(RuntimeError):
                GeminiAI()


class ProviderChainTests(SimpleTestCase):
    """Which provider answers, and what happens when the first one cannot."""

    class _Ok:
        def __init__(self):
            type(self).built = True

        def generate_text(self, prompt):
            return "answered"

        def generate_json(self, prompt, schema, schema_name="payload"):
            return {"caption": "answered"}

    class _Unconfigured:
        def __init__(self):
            raise RuntimeError("KEY is not set")

    class _Broken:
        def __init__(self):
            pass

        def generate_text(self, prompt):
            raise RuntimeError("out of quota")

        def generate_json(self, prompt, schema, schema_name="payload"):
            raise RuntimeError("out of quota")

    def chain(self, *factories):
        return providers.FallbackAI(providers=factories)

    def test_the_first_provider_answers_and_the_second_is_never_built(self):
        self._Ok.built = False
        second = type("_Second", (self._Ok,), {})
        second.built = False
        self.assertEqual(self.chain(self._Ok, second).generate_text("p"), "answered")
        self.assertFalse(second.built, "no reason to construct a client we won't use")

    def test_an_unconfigured_provider_is_skipped(self):
        """An unset GEMINI_API_KEY has to leave the bot exactly as it was."""
        chain = self.chain(self._Unconfigured, self._Ok)
        self.assertEqual(chain.generate_text("p"), "answered")

    def test_a_failing_provider_hands_over(self):
        chain = self.chain(self._Broken, self._Ok)
        self.assertEqual(chain.generate_json("p", CAPTION_SCHEMA),
                         {"caption": "answered"})

    def test_when_every_provider_fails_it_raises(self):
        """The callers already handle this: a price-led caption, or no post."""
        chain = self.chain(self._Unconfigured, self._Broken)
        with self.assertRaises(RuntimeError):
            chain.generate_text("p")

    def test_gemini_is_tried_before_cerebras(self):
        from ai.cerebras import CerebrasAI

        self.assertEqual(providers.PROVIDERS, (GeminiAI, CerebrasAI))

    def test_every_provider_implements_both_methods(self):
        """A chain is only a fallback if the fallback answers the same calls.

        Cheap, and it catches the real trap: generate_json was added to the
        Cerebras client later than generate_text, so a chain that had only the
        older client would fall back into an AttributeError.
        """
        for factory in providers.PROVIDERS:
            for method in ("generate_text", "generate_json"):
                self.assertTrue(
                    callable(getattr(factory, method, None)),
                    f"{factory.__name__} cannot answer {method}",
                )
