"""Which model answers, and what happens when it doesn't.

One provider was enough while the bot had one key. It now has two, and the
useful arrangement is an order rather than a choice: Gemini answers, and if
Gemini is unconfigured, out of quota or down, Cerebras does. Nothing upstream
has to know which one it got.

Callers ask for `ai_client()` instead of naming a provider, so switching the
order — or retiring one — is a change to this file alone.
"""

import logging

from ai.cerebras import CerebrasAI
from ai.gemini import GeminiAI

logger = logging.getLogger(__name__)

# In order. A provider whose key is missing raises on construction and is
# skipped, so an unset GEMINI_API_KEY leaves the bot exactly as it was.
PROVIDERS = (GeminiAI, CerebrasAI)


class FallbackAI:
    """The provider interface — generate_text, generate_json — over a chain.

    Both methods try each provider in turn and return the first answer. If every
    provider fails they raise, which is what the callers already handle: the
    listing caption falls back to its price-led lead line, the reel overlay to
    "Link in Bio", and the content pipeline skips the post rather than inventing
    one.
    """

    def __init__(self, providers=PROVIDERS):
        self.providers = providers

    def generate_text(self, prompt: str) -> str:
        return self._call("generate_text", prompt)

    def generate_json(self, prompt: str, schema: dict, schema_name: str = "payload"):
        return self._call("generate_json", prompt, schema, schema_name=schema_name)

    def _call(self, method, *args, **kwargs):
        last_exception = None
        for factory in self.providers:
            name = getattr(factory, "__name__", str(factory))
            try:
                client = factory()
            except Exception as exc:
                # Not configured, which is a normal state for a provider you
                # are not using — info, not an error.
                logger.info(f"Skipping {name}: {exc}")
                last_exception = last_exception or exc
                continue

            try:
                result = getattr(client, method)(*args, **kwargs)
                logger.info(f"{name} answered {method}")
                return result
            except Exception as exc:
                logger.error(f"{name} failed {method}: {exc}")
                last_exception = exc

        raise RuntimeError(f"No provider could answer {method}: {last_exception}")


def ai_client():
    """The client the bot should use. Ask for this, not for a provider."""
    return FallbackAI()
