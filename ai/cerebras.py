import json
import os
import logging
from django.conf import settings
from cerebras.cloud.sdk import Cerebras

logger = logging.getLogger(__name__)


class CerebrasAI:
    def __init__(self):
        self.cerebras = Cerebras(
            api_key=os.getenv("CEREBRAS_API_KEY"),
        )
        self.llama_4 = "llama-4-scout-17b-16e-instruct"
        self.llama3_1 = "llama3.1-8b"
        self.llama3_3 = "llama-3.3-70b"
        self.qwen_3 = "qwen-3-32b"

    def generate_text(self, prompt: str) -> str:
        last_exception = None

        for model in [self.llama_4, self.llama3_1, self.llama3_3, self.qwen_3]:
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
