import base64

import anthropic

from recipebot.backends import (
    ANTHROPIC_FAST,
    ANTHROPIC_STRONG,
    AnthropicBackend,
)
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
