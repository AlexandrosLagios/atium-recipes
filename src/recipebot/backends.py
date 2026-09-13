import base64

import anthropic

from .config import Config
from .llm import Backend, ImagePart, Part
from .models import ExtractedRecipe

ANTHROPIC_FAST = "claude-haiku-4-5"
ANTHROPIC_STRONG = "claude-sonnet-5"

MAX_TOKENS = 8000


def _anthropic_block(part: Part) -> dict:
    if isinstance(part, ImagePart):
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": part.media_type,
                "data": base64.standard_b64encode(part.data).decode("utf-8"),
            },
        }
    return {"type": "text", "text": part.text}


class AnthropicBackend:
    api_error = (anthropic.APIStatusError,)

    def __init__(
        self,
        client,
        fast: str = ANTHROPIC_FAST,
        strong: str = ANTHROPIC_STRONG,
    ):
        self.client = client
        self.fast = fast
        self.strong = strong

    def complete(
        self, model: str, system: str, parts: list[Part]
    ) -> ExtractedRecipe | None:
        # Haiku 4.5 rejects output_config.effort with a 400, so never pass it.
        response = self.client.messages.parse(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[
                {"role": "user", "content": [_anthropic_block(p) for p in parts]}
            ],
            output_format=ExtractedRecipe,
        )
        return response.parsed_output

    def is_rate_limited(self, exc: Exception) -> bool:
        return isinstance(exc, anthropic.APIStatusError) and exc.status_code == 429


def backend_from_config(cfg: Config) -> Backend:
    return AnthropicBackend(anthropic.Anthropic(api_key=cfg.anthropic_key))
