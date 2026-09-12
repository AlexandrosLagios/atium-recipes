import anthropic
import pytest

from recipebot.llm import HAIKU, SONNET, Extractor, image_block, text_block
from recipebot.models import ExtractedRecipe, Ingredient
from recipebot.notion import Vocabulary

VOCAB = Vocabulary(
    ingredients={"Chicken": "p1"},
    cuisines=["Chinese"],
    meals=["Side", "Dinner"],
    categories=["Staples"],
)

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

EMPTY = FULL.model_copy(update={"ingredients": [], "method": []})


class FakeMessages:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.models = []

    def parse(self, **kwargs):
        self.models.append(kwargs["model"])
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return type("R", (), {"parsed_output": outcome})()


class FakeClient:
    def __init__(self, outcomes):
        self.messages = FakeMessages(outcomes)


def test_haiku_alone_is_enough_when_the_parse_is_complete():
    client = FakeClient([FULL])

    result = Extractor(client).extract([text_block("body")], VOCAB)

    assert result == FULL
    assert client.messages.models == [HAIKU]


def test_an_empty_parse_escalates_to_sonnet():
    client = FakeClient([EMPTY, FULL])

    result = Extractor(client).extract([text_block("body")], VOCAB)

    assert result == FULL
    assert client.messages.models == [HAIKU, SONNET]


def test_an_api_error_on_haiku_escalates_to_sonnet():
    error = anthropic.APIStatusError(
        "bad request",
        response=type("Resp", (), {"status_code": 400, "headers": {}, "request": None})(),
        body=None,
    )
    client = FakeClient([error, FULL])

    result = Extractor(client).extract([text_block("body")], VOCAB)

    assert result == FULL
    assert client.messages.models == [HAIKU, SONNET]


def test_a_429_on_haiku_raises_and_never_calls_sonnet():
    error = anthropic.APIStatusError(
        "rate limited",
        response=type("Resp", (), {"status_code": 429, "headers": {}, "request": None})(),
        body=None,
    )
    client = FakeClient([error, FULL])

    with pytest.raises(anthropic.APIStatusError):
        Extractor(client).extract([text_block("body")], VOCAB)

    assert client.messages.models == [HAIKU]


def test_two_empty_parses_return_none():
    client = FakeClient([EMPTY, EMPTY])

    assert Extractor(client).extract([text_block("body")], VOCAB) is None


def test_an_api_error_on_sonnet_is_raised():
    error = anthropic.APIStatusError(
        "bad",
        response=type("Resp", (), {"status_code": 500, "headers": {}, "request": None})(),
        body=None,
    )
    client = FakeClient([EMPTY, error])

    with pytest.raises(anthropic.APIStatusError):
        Extractor(client).extract([text_block("body")], VOCAB)


def test_the_vocabulary_reaches_the_system_prompt():
    client = FakeClient([FULL])
    seen = {}

    original = client.messages.parse

    def capture(**kwargs):
        seen.update(kwargs)
        return original(**kwargs)

    client.messages.parse = capture
    Extractor(client).extract([text_block("body")], VOCAB)

    assert "Chicken" in seen["system"]
    assert "Dinner" in seen["system"]
    assert "Staples" in seen["system"]


def test_image_block_is_base64():
    block = image_block(b"\x89PNG", "image/png")

    assert block["type"] == "image"
    assert block["source"]["type"] == "base64"
    assert block["source"]["media_type"] == "image/png"
