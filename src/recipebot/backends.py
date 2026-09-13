import base64

import anthropic
from google import genai
from google.genai import errors, types

from .config import Config
from .llm import Backend, ImagePart, Part
from .models import ExtractedRecipe

ANTHROPIC_FAST = "claude-haiku-4-5"
ANTHROPIC_STRONG = "claude-sonnet-5"
GEMINI_FAST = "gemini-3.1-flash-lite"
GEMINI_STRONG = "gemini-3.7-flash"

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


def _gemini_part(part: Part) -> types.Part:
    if isinstance(part, ImagePart):
        return types.Part.from_bytes(data=part.data, mime_type=part.media_type)
    return types.Part.from_text(text=part.text)


class GeminiBackend:
    api_error = (errors.APIError,)

    def __init__(
        self,
        client,
        fast: str = GEMINI_FAST,
        strong: str = GEMINI_STRONG,
    ):
        self.client = client
        self.fast = fast
        self.strong = strong

    def complete(
        self, model: str, system: str, parts: list[Part]
    ) -> ExtractedRecipe | None:
        response = self.client.models.generate_content(
            model=model,
            contents=[_gemini_part(p) for p in parts],
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=ExtractedRecipe,
            ),
        )
        # The SDK swallows a validation or JSON error and leaves parsed None,
        # which the escalation loop reads as an empty parse.
        return response.parsed

    def is_rate_limited(self, exc: Exception) -> bool:
        return isinstance(exc, errors.APIError) and exc.code == 429


def backend_from_config(cfg: Config) -> Backend:
    if cfg.llm_provider == "gemini":
        return GeminiBackend(
            genai.Client(api_key=cfg.llm_api_key),
            fast=cfg.model_fast or GEMINI_FAST,
            strong=cfg.model_strong or GEMINI_STRONG,
        )
    if cfg.llm_provider == "anthropic":
        return AnthropicBackend(
            anthropic.Anthropic(api_key=cfg.llm_api_key),
            fast=cfg.model_fast or ANTHROPIC_FAST,
            strong=cfg.model_strong or ANTHROPIC_STRONG,
        )
    raise RuntimeError(f"unknown LLM provider: {cfg.llm_provider!r}")
