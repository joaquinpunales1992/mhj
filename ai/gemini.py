"""Gemini, spoken to over plain REST.

Same two methods as CerebrasAI — generate_text and generate_json — because
that is the whole interface the bot uses, and a provider that implements it can
be swapped in without touching a caller.

REST rather than the google-genai SDK: it would add pydantic, httpx and
websockets to a box with very little RAM, and everything the bot needs is one
POST. `requests` is already a dependency.
"""

import json
import logging
import os

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"

# Gemini's schema dialect is a subset of JSON Schema (an OpenAPI 3 Schema
# object). Anything else in a caller's schema is dropped rather than passed
# through: sending `additionalProperties`, which both of our schemas set, is
# rejected by the API.
_SCHEMA_KEYS = {
    "type", "format", "description", "nullable", "enum", "items",
    "properties", "required", "propertyOrdering", "minItems", "maxItems",
}


def _to_gemini_schema(schema):
    """Translate a JSON Schema into the subset responseSchema accepts.

    Types are upper-cased (the enum is STRING/OBJECT/ARRAY/…, and the lower-case
    spelling our schemas use is not accepted), and unsupported keys are dropped.
    Recursive, so a nested schema added later needs no work here.
    """
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key not in _SCHEMA_KEYS:
            continue
        if key == "type" and isinstance(value, str):
            out[key] = value.upper()
        elif key == "properties" and isinstance(value, dict):
            out[key] = {k: _to_gemini_schema(v) for k, v in value.items()}
        elif key == "items":
            out[key] = _to_gemini_schema(value)
        else:
            out[key] = value
    return out


class GeminiAI:
    # In order of preference. Whatever the account does not have is skipped and
    # whatever else it does have is used as a further fallback — same approach
    # as the Cerebras client, and for the same reason: model names get retired
    # and a caption should not stop going out over a rename.
    PREFERRED_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash"]

    # …but not just anything. Cerebras lists a handful of chat models, so
    # "whatever else is there" is a safe fallback. Google lists dozens, most of
    # them specialised, and they all advertise generateContent — a caption was
    # attempted against gemini-2.5-pro-tts, a text-to-speech model, which
    # answered with a quota error naming itself. Fallbacks are text models only.
    NOT_TEXT = (
        "tts", "image", "imagen", "veo", "embedding", "aqa", "audio", "live",
        "vision", "guard",
    )

    # Captions are a hundred characters. The ceiling is high anyway because the
    # 2.5 models spend output tokens thinking before they answer, and a budget
    # tight enough to fit only the answer returns an empty one.
    MAX_OUTPUT_TOKENS = 2048

    TIMEOUT = 60

    def __init__(self, api_key=None):
        # settings first: it reads .env by absolute path, which is what makes
        # this work under cron, where the working directory is not the project.
        self.api_key = (
            api_key
            or getattr(settings, "GEMINI_API_KEY", "")
            or os.getenv("GEMINI_API_KEY", "")
        )
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self._models = None

    def _headers(self):
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    def _resolve_models(self):
        """Preferred models first, then anything else that can generate.

        Cached per instance: a client is built per call site, and paying for a
        model listing on every caption is a round trip for nothing.
        """
        if self._models is not None:
            return self._models

        try:
            response = requests.get(
                f"{API_ROOT}/models", headers=self._headers(), timeout=self.TIMEOUT
            )
            response.raise_for_status()
            available = [
                model["name"].removeprefix("models/")
                for model in response.json().get("models", [])
                if "generateContent" in model.get("supportedGenerationMethods", [])
            ]
        except Exception as exc:
            logger.error(f"Could not list Gemini models, using preferred list: {exc}")
            self._models = list(self.PREFERRED_MODELS)
            return self._models

        preferred = [m for m in self.PREFERRED_MODELS if m in available]
        others = [
            model for model in available
            if model not in self.PREFERRED_MODELS
            and model.startswith("gemini-")
            and not any(word in model for word in self.NOT_TEXT)
        ]
        resolved = preferred + others

        missing = [m for m in self.PREFERRED_MODELS if m not in available]
        if missing:
            logger.warning(
                f"Preferred Gemini models unavailable: {missing}. Using: {resolved}"
            )
        self._models = resolved or list(self.PREFERRED_MODELS)
        return self._models

    def generate_text(self, prompt: str) -> str:
        """One string back, so this is a drop-in for the Cerebras client."""
        return self.generate_json(prompt, self._caption_schema())["caption"]

    def generate_json(self, prompt: str, schema: dict, schema_name: str = "payload"):
        """Structured output against `schema`, trying each model in turn.

        `schema_name` is accepted and ignored: Gemini has no name for a schema,
        and the parameter exists so this class and CerebrasAI can be called
        interchangeably.
        """
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": _to_gemini_schema(schema),
                "maxOutputTokens": self.MAX_OUTPUT_TOKENS,
            },
        }

        last_exception = None
        for model in self._resolve_models():
            try:
                response = requests.post(
                    f"{API_ROOT}/models/{model}:generateContent",
                    headers=self._headers(), json=payload, timeout=self.TIMEOUT,
                )
                if response.status_code != 200:
                    # The body says which of quota, key and model is wrong;
                    # the status code on its own has sent me looking in the
                    # wrong place before.
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

                obj = json.loads(_first_text(response.json()))
                logger.info(f"Generated with model {model}: {obj}")
                return obj
            except Exception as exc:
                logger.error(f"Error generating with model {model}: {exc}")
                last_exception = exc
        raise RuntimeError(f"All models failed to generate: {last_exception}")

    def _caption_schema(self):
        return {
            "type": "object",
            "properties": {"caption": {"type": "string"}},
            "required": ["caption"],
            "additionalProperties": False,
        }


def _first_text(body):
    """The generated text out of a generateContent response.

    Raises with the finish reason rather than an IndexError when there are no
    parts: a response can come back 200 with the answer withheld — a safety
    block, or thinking that used the whole token budget — and "MAX_TOKENS" in
    the log is the difference between a five-minute fix and an afternoon.
    """
    candidates = body.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"No candidates in response: {body}")

    candidate = candidates[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts).strip()
    if not text:
        raise RuntimeError(
            f"Empty response (finishReason={candidate.get('finishReason')}): {body}"
        )
    return text
