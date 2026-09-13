import base64

import anthropic
import pytest
from google.genai import errors

from recipebot.backends import (
    ANTHROPIC_FAST,
    ANTHROPIC_STRONG,
    GEMINI_FAST,
    GEMINI_STRONG,
    AnthropicBackend,
    GeminiBackend,
    backend_from_config,
)
from recipebot.config import Config
from recipebot.llm import image_block, text_block
from recipebot.models import ExtractedRecipe, Ingredient

FULL = ExtractedRecipe(
    name="Pickles",
    cuisine="Chinese",
    meal=["Side"],
    difficulty="Easy",
    time_min=745,
    servings=4,
    ingredients=[Ingredient(name="Cucumber", quantity="2")],
    method=["Salt.", "Rest."],
)

PNG = b"\x89PNG\r\n\x1a\n"


def anthropic_error(status: int) -> anthropic.APIStatusError:
    response = type(
        "Resp", (), {"status_code": status, "headers": {}, "request": None}
    )()
    return anthropic.APIStatusError("boom", response=response, body=None)


class FakeMessages:
    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return type("R", (), {"parsed_output": self.parsed})()


class FakeAnthropicClient:
    def __init__(self, parsed=FULL):
        self.messages = FakeMessages(parsed)


def test_anthropic_defaults_to_the_documented_model_pair():
    backend = AnthropicBackend(FakeAnthropicClient())

    assert (backend.fast, backend.strong) == (ANTHROPIC_FAST, ANTHROPIC_STRONG)
    assert (ANTHROPIC_FAST, ANTHROPIC_STRONG) == (
        "claude-haiku-4-5",
        "claude-sonnet-5",
    )


def test_anthropic_accepts_overridden_model_ids():
    backend = AnthropicBackend(FakeAnthropicClient(), fast="f", strong="s")

    assert (backend.fast, backend.strong) == ("f", "s")


def test_anthropic_sends_the_structured_output_call_shape():
    client = FakeAnthropicClient()

    AnthropicBackend(client).complete("m", "system text", [text_block("body")])

    call = client.messages.calls[0]
    assert call["model"] == "m"
    assert call["system"] == "system text"
    assert call["output_format"] is ExtractedRecipe
    assert call["max_tokens"] > 0
    assert call["messages"][0]["role"] == "user"


def test_anthropic_translates_a_text_part():
    client = FakeAnthropicClient()

    AnthropicBackend(client).complete("m", "s", [text_block("body")])

    content = client.messages.calls[0]["messages"][0]["content"]
    assert content == [{"type": "text", "text": "body"}]


def test_anthropic_base64_encodes_an_image_part_and_keeps_the_order():
    client = FakeAnthropicClient()

    AnthropicBackend(client).complete(
        "m", "s", [image_block(PNG, "image/png"), text_block("body")]
    )

    content = client.messages.calls[0]["messages"][0]["content"]
    assert content[0] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(PNG).decode("utf-8"),
        },
    }
    assert content[1]["type"] == "text"


def test_anthropic_returns_the_parsed_output():
    backend = AnthropicBackend(FakeAnthropicClient())

    assert backend.complete("m", "s", [text_block("body")]) == FULL


def test_anthropic_returns_none_when_nothing_parsed():
    backend = AnthropicBackend(FakeAnthropicClient(parsed=None))

    assert backend.complete("m", "s", [text_block("body")]) is None


def test_anthropic_recognises_a_rate_limit():
    backend = AnthropicBackend(FakeAnthropicClient())

    assert backend.is_rate_limited(anthropic_error(429)) is True
    assert backend.is_rate_limited(anthropic_error(400)) is False
    assert backend.is_rate_limited(anthropic_error(500)) is False
    assert backend.is_rate_limited(RuntimeError("unrelated")) is False


def test_anthropic_owns_its_sdk_error_type():
    assert AnthropicBackend.api_error == (anthropic.APIStatusError,)


class FakeModels:
    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return type("R", (), {"parsed": self.parsed})()


class FakeGeminiClient:
    def __init__(self, parsed=FULL):
        self.models = FakeModels(parsed)


def test_gemini_defaults_to_the_documented_model_pair():
    backend = GeminiBackend(FakeGeminiClient())

    assert (backend.fast, backend.strong) == (GEMINI_FAST, GEMINI_STRONG)
    assert (GEMINI_FAST, GEMINI_STRONG) == (
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
    )


def test_gemini_accepts_overridden_model_ids():
    backend = GeminiBackend(FakeGeminiClient(), fast="f", strong="s")

    assert (backend.fast, backend.strong) == ("f", "s")


def test_gemini_sends_the_structured_output_call_shape():
    client = FakeGeminiClient()

    GeminiBackend(client).complete("m", "system text", [text_block("body")])

    call = client.models.calls[0]
    assert call["model"] == "m"
    assert call["config"].system_instruction == "system text"
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_schema is ExtractedRecipe


def test_gemini_translates_a_text_part():
    client = FakeGeminiClient()

    GeminiBackend(client).complete("m", "s", [text_block("body")])

    contents = client.models.calls[0]["contents"]
    assert [part.text for part in contents] == ["body"]


def test_gemini_sends_raw_image_bytes_and_keeps_the_order():
    client = FakeGeminiClient()

    GeminiBackend(client).complete(
        "m", "s", [image_block(PNG, "image/png"), text_block("body")]
    )

    contents = client.models.calls[0]["contents"]
    assert contents[0].inline_data.data == PNG
    assert contents[0].inline_data.mime_type == "image/png"
    assert contents[1].text == "body"


def test_gemini_returns_the_parsed_result():
    backend = GeminiBackend(FakeGeminiClient())

    assert backend.complete("m", "s", [text_block("body")]) == FULL


def test_gemini_returns_none_when_nothing_parsed():
    backend = GeminiBackend(FakeGeminiClient(parsed=None))

    assert backend.complete("m", "s", [text_block("body")]) is None


def test_gemini_recognises_a_rate_limit():
    backend = GeminiBackend(FakeGeminiClient())

    assert backend.is_rate_limited(errors.ClientError(429, {})) is True
    assert backend.is_rate_limited(errors.ClientError(400, {})) is False
    assert backend.is_rate_limited(errors.ServerError(500, {})) is False
    assert backend.is_rate_limited(RuntimeError("unrelated")) is False


def test_gemini_owns_its_sdk_error_type():
    assert GeminiBackend.api_error == (errors.APIError,)
    assert issubclass(errors.ClientError, errors.APIError)
    assert issubclass(errors.ServerError, errors.APIError)


def config(**overrides) -> Config:
    fields = {
        "telegram_token": "tok",
        "allowed_user_id": 1,
        "notion_token": "ntn",
        "recipes_ds": "ds-r",
        "ingredients_ds": "ds-i",
        "llm_provider": "gemini",
        "llm_api_key": "key",
    }
    return Config(**{**fields, **overrides})


def test_backend_from_config_builds_gemini_with_its_defaults():
    backend = backend_from_config(config())

    assert isinstance(backend, GeminiBackend)
    assert (backend.fast, backend.strong) == (GEMINI_FAST, GEMINI_STRONG)


def test_backend_from_config_builds_anthropic_with_its_defaults():
    backend = backend_from_config(config(llm_provider="anthropic"))

    assert isinstance(backend, AnthropicBackend)
    assert (backend.fast, backend.strong) == (ANTHROPIC_FAST, ANTHROPIC_STRONG)


def test_backend_from_config_applies_the_model_overrides():
    backend = backend_from_config(config(model_fast="f", model_strong="s"))

    assert (backend.fast, backend.strong) == ("f", "s")

    backend = backend_from_config(
        config(llm_provider="anthropic", model_fast="f", model_strong="s")
    )

    assert (backend.fast, backend.strong) == ("f", "s")


def test_backend_from_config_refuses_an_unknown_provider():
    with pytest.raises(RuntimeError, match="ollama"):
        backend_from_config(config(llm_provider="ollama"))
