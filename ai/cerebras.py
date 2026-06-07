import json
import os
import logging
from django.conf import settings
from cerebras.cloud.sdk import Cerebras

logger = logging.getLogger(__name__)


class CerebrasAI:
    # Models we'd like to use, in order of preference. Any of these that the
    # account no longer has access to are skipped automatically (see
    # _resolve_models), and any other models the account *does* have are used
    # as additional fallbacks. This keeps captions working even when Cerebras
    # renames or retires models.
    PREFERRED_MODELS = ["zai-glm-4.7", "gpt-oss-120b"]

    def __init__(self):
        self.cerebras = Cerebras(
            api_key=os.getenv("CEREBRAS_API_KEY"),
        )

    def _resolve_models(self):
        """Order preferred models first, then any other available models.

        Falls back to PREFERRED_MODELS as-is if the model list can't be
        fetched, so a transient listing failure never blocks generation.
        """
        try:
            available = [m.id for m in self.cerebras.models.list().data]
        except Exception as e:
            logger.error(f"Could not list Cerebras models, using preferred list: {e}")
            return list(self.PREFERRED_MODELS)

        preferred = [m for m in self.PREFERRED_MODELS if m in available]
        others = [m for m in available if m not in self.PREFERRED_MODELS]
        resolved = preferred + others

        missing = [m for m in self.PREFERRED_MODELS if m not in available]
        if missing:
            logger.warning(
                f"Preferred Cerebras models unavailable: {missing}. Using: {resolved}"
            )
        return resolved or list(self.PREFERRED_MODELS)

    def generate_text(self, prompt: str) -> str:
        last_exception = None

        for model in self._resolve_models():
            try:
                resp = self.cerebras.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=model,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "caption_schema",
                            "strict": True,
                            "schema": self._get_scehma(),
                        },
                    },
                )
                obj = json.loads(resp.choices[0].message.content)
                logger.info(f"Caption generated with model {model}: {obj['caption']}")
                return obj["caption"]
            except Exception as e:
                logger.error(f"Error generating caption with model {model}: {e}")
                last_exception = e
        raise RuntimeError(f"All models failed to generate caption: {last_exception}")

    def _get_scehma(self):
        return {
            "type": "object",
            "properties": {"caption": {"type": "string"}},
            "required": ["caption"],
            "additionalProperties": False,
        }
