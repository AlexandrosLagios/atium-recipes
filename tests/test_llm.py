import pytest

from recipebot.llm import Extractor, ImagePart, TextPart, image_block, text_block
from recipebot.models import ExtractedRecipe, Ingredient, Recipe, RecipePatch
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
NO_METHOD = FULL.model_copy(update={"method": []})
NO_INGREDIENTS = FULL.model_copy(update={"ingredients": []})

FAST = "fast-model"
STRONG = "strong-model"


class FakeApiError(Exception):
    def __init__(self, status: int):
        super().__init__(f"status {status}")
        self.status = status


class FakeBackend:
    api_error = (FakeApiError,)

    def __init__(self, outcomes):
        self.fast = FAST
        self.strong = STRONG
        self.outcomes = list(outcomes)
        self.models = []
        self.systems = []
        self.parts = []
        self.schemas = []

    def complete(self, model, system, parts, schema=ExtractedRecipe):
        self.models.append(model)
        self.systems.append(system)
        self.parts.append(parts)
        self.schemas.append(schema)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def is_rate_limited(self, exc):
        return exc.status == 429


def test_the_fast_model_alone_is_enough_when_the_parse_is_complete():
    backend = FakeBackend([FULL])

    result = Extractor(backend).extract([text_block("body")], VOCAB)

    assert result == FULL
    assert backend.models == [FAST]


def test_an_empty_parse_escalates_to_the_strong_model():
    backend = FakeBackend([EMPTY, FULL])

    result = Extractor(backend).extract([text_block("body")], VOCAB)

    assert result == FULL
    assert backend.models == [FAST, STRONG]


def test_no_result_at_all_escalates_to_the_strong_model():
    backend = FakeBackend([None, FULL])

    result = Extractor(backend).extract([text_block("body")], VOCAB)

    assert result == FULL
    assert backend.models == [FAST, STRONG]


def test_a_parse_without_a_method_escalates():
    backend = FakeBackend([NO_METHOD, FULL])

    assert Extractor(backend).extract([text_block("body")], VOCAB) == FULL
    assert backend.models == [FAST, STRONG]


def test_a_parse_without_ingredients_escalates():
    backend = FakeBackend([NO_INGREDIENTS, FULL])

    assert Extractor(backend).extract([text_block("body")], VOCAB) == FULL
    assert backend.models == [FAST, STRONG]


def test_an_api_error_on_the_fast_model_escalates():
    backend = FakeBackend([FakeApiError(400), FULL])

    result = Extractor(backend).extract([text_block("body")], VOCAB)

    assert result == FULL
    assert backend.models == [FAST, STRONG]


def test_a_rate_limit_on_the_fast_model_raises_and_never_escalates():
    backend = FakeBackend([FakeApiError(429), FULL])

    with pytest.raises(FakeApiError):
        Extractor(backend).extract([text_block("body")], VOCAB)

    assert backend.models == [FAST]


def test_an_api_error_on_the_strong_model_is_raised():
    backend = FakeBackend([EMPTY, FakeApiError(500)])

    with pytest.raises(FakeApiError):
        Extractor(backend).extract([text_block("body")], VOCAB)

    assert backend.models == [FAST, STRONG]


def test_a_rate_limit_on_the_strong_model_is_raised():
    backend = FakeBackend([EMPTY, FakeApiError(429)])

    with pytest.raises(FakeApiError):
        Extractor(backend).extract([text_block("body")], VOCAB)

    assert backend.models == [FAST, STRONG]


def test_an_error_the_backend_does_not_own_never_buys_a_second_call():
    backend = FakeBackend([TypeError("bug in our own code"), FULL])

    with pytest.raises(TypeError):
        Extractor(backend).extract([text_block("body")], VOCAB)

    assert backend.models == [FAST]


def test_two_empty_parses_return_none():
    backend = FakeBackend([EMPTY, EMPTY])

    assert Extractor(backend).extract([text_block("body")], VOCAB) is None
    assert backend.models == [FAST, STRONG]


def test_the_vocabulary_reaches_the_system_prompt():
    backend = FakeBackend([FULL])

    Extractor(backend).extract([text_block("body")], VOCAB)

    assert "Chicken" in backend.systems[0]
    assert "Dinner" in backend.systems[0]
    assert "Staples" in backend.systems[0]


def test_the_parts_reach_the_backend_unchanged():
    backend = FakeBackend([FULL])
    parts = [image_block(b"\x89PNG", "image/png"), text_block("body")]

    Extractor(backend).extract(parts, VOCAB)

    assert backend.parts[0] == parts


def test_text_block_carries_the_text():
    assert text_block("body") == TextPart("body")


def test_image_block_keeps_the_raw_bytes_and_the_media_type():
    part = image_block(b"\x89PNG", "image/png")

    assert part == ImagePart(b"\x89PNG", "image/png")
    assert part.data == b"\x89PNG"


RECIPE = Recipe.from_extracted(
    FULL, source="Web", source_url="https://example.com/p", source_text="the page"
)


def test_a_patch_changes_only_the_fields_it_set():
    backend = FakeBackend([RecipePatch(servings=2)])

    result = Extractor(backend).patch(FULL, "servings is 2, not 4", VOCAB)

    assert result == FULL.model_copy(update={"servings": 2})


def test_a_patch_over_a_recipe_keeps_the_fields_a_recipe_adds():
    backend = FakeBackend([RecipePatch(servings=2)])

    result = Extractor(backend).patch(RECIPE, "servings is 2", VOCAB)

    assert isinstance(result, Recipe)
    assert result.source_url == "https://example.com/p"
    assert result.source_text == "the page"
    assert result.servings == 2


def test_a_patch_runs_once_on_the_strong_model():
    backend = FakeBackend([RecipePatch(servings=2)])

    Extractor(backend).patch(FULL, "servings is 2", VOCAB)

    assert backend.models == [STRONG]
    assert backend.schemas == [RecipePatch]


def test_no_patch_at_all_returns_none():
    backend = FakeBackend([None])

    assert Extractor(backend).patch(FULL, "make it better", VOCAB) is None
    assert backend.models == [STRONG]


def test_the_patch_call_carries_the_recipe_the_instruction_and_the_vocabulary():
    backend = FakeBackend([RecipePatch(servings=2)])

    Extractor(backend).patch(RECIPE, "servings is 2", VOCAB)

    sent = backend.parts[0][0].text
    assert '"servings":4' in sent.replace(" ", "")
    assert "servings is 2" in sent
    assert "Chicken" in backend.systems[0]
    # The stored source text is already extracted, so re-sending it would pay
    # for the whole page again and hand the model a second set of numbers.
    assert "the page" not in sent


def test_a_patch_that_empties_a_list_is_applied():
    backend = FakeBackend([RecipePatch(method=["Salt."])])

    result = Extractor(backend).patch(FULL, "drop the resting step", VOCAB)

    assert result.method == ["Salt."]
