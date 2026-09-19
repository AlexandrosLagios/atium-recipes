import base64
import json

import anthropic
import httpx2
import pytest
from google.genai import errors, types

from recipebot.backends import (
    ANTHROPIC_FAST,
    ANTHROPIC_STRONG,
    GEMINI_ATTEMPTS,
    GEMINI_FAST,
    GEMINI_STRONG,
    GEMINI_TIMEOUT_MS,
    MAX_TOKENS,
    AnthropicBackend,
    GeminiBackend,
    _anthropic_block,
    _gemini_part,
    backend_from_config,
)
from recipebot.config import Config
from recipebot.llm import Extractor, image_block, text_block
from recipebot.models import ExtractedRecipe, Ingredient, RecipePatch
from recipebot.notion import Vocabulary

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
        "gemini-3.1-flash-lite",
        "gemini-3.7-flash",
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
        "allowed_user_ids": frozenset({1}),
        "notion_client_id": "c",
        "notion_client_secret": "s",
        "notion_redirect_uri": "https://bot.example/oauth/callback",
        "oauth_callback_port": 8080,
        "db_path": ":memory:",
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


VOCAB = Vocabulary(
    ingredients={"Chicken": "p1"},
    cuisines=["Chinese"],
    meals=["Side"],
    categories=["Staples"],
)

TRUNCATED = FULL.model_dump_json()[: len(FULL.model_dump_json()) // 2]


def anthropic_over_transport(text: str, models: list | None = None):
    """A real Anthropic client whose transport replies with `text` as the body."""

    def handler(request):
        if models is not None:
            models.append(json.loads(request.content)["model"])
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": ANTHROPIC_FAST,
                "content": [{"type": "text", "text": text}],
                "stop_reason": "max_tokens",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": MAX_TOKENS},
            },
        )

    return anthropic.Anthropic(
        api_key="test-key-not-real",
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def test_anthropic_reads_output_truncated_at_max_tokens_as_an_empty_parse():
    backend = AnthropicBackend(anthropic_over_transport(TRUNCATED))

    assert backend.complete(ANTHROPIC_FAST, "s", [text_block("body")]) is None


def test_a_truncated_anthropic_parse_escalates_to_the_strong_model():
    models = []
    backend = AnthropicBackend(anthropic_over_transport(TRUNCATED, models))

    assert Extractor(backend).extract([text_block("body")], VOCAB) is None
    assert models == [ANTHROPIC_FAST, ANTHROPIC_STRONG]


def test_a_complete_anthropic_body_over_the_transport_still_parses():
    backend = AnthropicBackend(anthropic_over_transport(FULL.model_dump_json()))

    assert backend.complete(ANTHROPIC_FAST, "s", [text_block("body")]) == FULL


def test_gemini_turns_off_the_automatic_function_calling_loop():
    client = FakeGeminiClient()

    GeminiBackend(client).complete("m", "s", [text_block("body")])

    afc = client.models.calls[0]["config"].automatic_function_calling
    assert afc is not None
    assert afc.disable is True


def test_backend_from_config_gives_the_gemini_client_retries():
    options = backend_from_config(config()).client._api_client._http_options

    assert options.retry_options is not None
    assert options.retry_options.attempts == GEMINI_ATTEMPTS
    assert GEMINI_ATTEMPTS > 1


def test_backend_from_config_gives_the_gemini_client_a_request_timeout():
    options = backend_from_config(config()).client._api_client._http_options

    assert options.timeout == GEMINI_TIMEOUT_MS
    # The SDK reads HttpOptions.timeout as milliseconds.
    assert 0 < GEMINI_TIMEOUT_MS / 1000 <= 180


def test_a_pdf_becomes_an_anthropic_document_block_not_an_image_block():
    block = _anthropic_block(image_block(b"%PDF-1.4", "application/pdf"))

    assert block["type"] == "document"
    assert block["source"]["media_type"] == "application/pdf"


def test_an_image_still_becomes_an_anthropic_image_block():
    assert _anthropic_block(image_block(PNG, "image/png"))["type"] == "image"


def test_gemini_carries_a_pdf_through_as_inline_data():
    part = _gemini_part(image_block(b"%PDF-1.4", "application/pdf"))

    assert part.inline_data.mime_type == "application/pdf"
    assert part.inline_data.data == b"%PDF-1.4"


def test_anthropic_sends_the_schema_the_caller_asked_for():
    client = FakeAnthropicClient()

    AnthropicBackend(client).complete("m", "s", [text_block("body")], RecipePatch)

    assert client.messages.calls[0]["output_format"] is RecipePatch


def test_gemini_sends_the_schema_the_caller_asked_for():
    client = FakeGeminiClient()

    GeminiBackend(client).complete("m", "s", [text_block("body")], RecipePatch)

    assert client.models.calls[0]["config"].response_schema is RecipePatch


# Gemini rejects a response schema it cannot express, so the all-optional patch
# model has to survive the SDK's own conversion before it is ever sent.
def test_the_patch_schema_survives_geminis_own_conversion():
    schema = types.Schema.from_json_schema(
        json_schema=types.JSONSchema(**RecipePatch.model_json_schema())
    )

    assert set(schema.properties) == set(RecipePatch.model_fields)
    assert all(prop.nullable for prop in schema.properties.values())
