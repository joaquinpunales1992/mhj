from django.conf import settings

# langchain-huggingface pulls in transformers (~300 MB). Import lazily so that
# importing this module doesn't require the dependency on hosts that don't use
# HuggingFaceAI (e.g. the cron-driven Instagram bot uses Cerebras instead).
try:
    from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint
except ImportError:
    ChatHuggingFace = None
    HuggingFaceEndpoint = None


class HuggingFaceAI:
    def __init__(
        self, max_new_tokens: int = 512, top_p: float = 0.95, temperature: float = 0.5
    ) -> None:
        if ChatHuggingFace is None:
            raise ImportError(
                "langchain-huggingface is not installed. "
                "Add `langchain-huggingface` to requirements to use HuggingFaceAI."
            )
        self.endpoint_url = settings.HUGGING_FACE_AI_ENDPOINT_URL
        self.max_new_tokens = max_new_tokens
        self.top_p = top_p
        self.temperature = temperature
        self.api_token = settings.HUGGING_FACE_AI_TOKEN

    def _create_chat_model(self, llm: HuggingFaceEndpoint) -> ChatHuggingFace:
        return ChatHuggingFace(llm=llm)

    def invoke_ai(self, instruction: str) -> str:
        llm = HuggingFaceEndpoint(
            endpoint_url=self.endpoint_url,
            max_new_tokens=self.max_new_tokens,
            top_p=self.top_p,
            temperature=self.temperature,
            huggingfacehub_api_token=self.api_token,
        )
        chat_model = self._create_chat_model(llm=llm)

        messages = [("human", instruction)]
        response = chat_model.invoke(messages)
        return response.content
