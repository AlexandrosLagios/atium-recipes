import pytest

from recipebot import extract
from recipebot.llm import ImagePart, TextPart
from recipebot.models import ExtractedRecipe, Ingredient
from recipebot.notion import Vocabulary
from recipebot.scrape import ScrapeResult
from recipebot.social import SocialBlocked, SocialResult

VOCAB = Vocabulary(ingredients={}, cuisines=[], meals=[], categories=[])

MODEL_SAID = ExtractedRecipe(
    name="Model title",
    cuisine="Chinese",
    meal=["Side"],
    difficulty="Easy",
    time_min=10,
    servings=1,
    ingredients=[Ingredient(name="Cucumber", quantity="2 medium")],
    method=["Model step."],
)


class StubExtractor:
    def __init__(self, result=MODEL_SAID):
        self.result = result
        self.calls = []

    def extract(self, blocks, vocab):
        self.calls.append(blocks)
        return self.result


def test_source_for_recognises_each_host():
    assert extract.source_for("https://www.instagram.com/p/abc/") == "Instagram"
    assert extract.source_for("https://vm.tiktok.com/x/") == "TikTok"
    assert extract.source_for("https://youtu.be/x") == "YouTube"
    assert extract.source_for("https://redhousespice.com/x/") == "Web"


def test_a_scraped_page_is_high_confidence_and_the_scraper_facts_win(monkeypatch):
    scraped = ScrapeResult(
        name="Overnight pickled vegetables",
        time_min=745,
        servings=4,
        ingredients=["2 medium cucumbers"],
        method=["Salt.", "Rest."],
        image_url="https://example.com/cover.jpg",
    )
    monkeypatch.setattr(extract, "fetch_html", lambda url: "<html></html>")
    monkeypatch.setattr(extract, "scrape_jsonld", lambda html, url: scraped)
    stub = StubExtractor()

    recipe = extract.from_url("https://redhousespice.com/x/?utm=1", stub, VOCAB)

    assert recipe.high_confidence is True
    assert recipe.name == "Overnight pickled vegetables"
    assert recipe.time_min == 745
    assert recipe.servings == 4
    assert recipe.method == ["Salt.", "Rest."]
    assert recipe.image_url == "https://example.com/cover.jpg"
    assert recipe.source_url == "https://redhousespice.com/x/"
    assert recipe.ingredients[0].name == "Cucumber"


def test_the_model_estimate_survives_when_the_scraper_found_no_time_or_servings(monkeypatch):
    scraped = ScrapeResult(
        name="Overnight pickled vegetables",
        time_min=0,
        servings=0,
        ingredients=["2 medium cucumbers"],
        method=["Salt.", "Rest."],
        image_url="https://example.com/cover.jpg",
    )
    monkeypatch.setattr(extract, "fetch_html", lambda url: "<html></html>")
    monkeypatch.setattr(extract, "scrape_jsonld", lambda html, url: scraped)
    stub = StubExtractor()

    recipe = extract.from_url("https://example.com/no-time-data", stub, VOCAB)

    assert recipe.time_min == 10
    assert recipe.servings == 1


def test_time_min_takes_the_models_resting_aware_figure_over_a_smaller_scraped_one(monkeypatch):
    scraped = ScrapeResult(
        name="Overnight pickled vegetables",
        time_min=30,
        servings=4,
        ingredients=["2 medium cucumbers"],
        method=["Salt.", "Rest."],
    )
    model_said = MODEL_SAID.model_copy(update={"time_min": 745})
    monkeypatch.setattr(extract, "fetch_html", lambda url: "<html></html>")
    monkeypatch.setattr(extract, "scrape_jsonld", lambda html, url: scraped)
    stub = StubExtractor(result=model_said)

    recipe = extract.from_url("https://redhousespice.com/x/", stub, VOCAB)

    assert recipe.time_min == 745


def test_time_min_keeps_the_larger_scraped_figure_over_a_smaller_model_one(monkeypatch):
    scraped = ScrapeResult(
        name="Overnight pickled vegetables",
        time_min=745,
        servings=4,
        ingredients=["2 medium cucumbers"],
        method=["Salt.", "Rest."],
    )
    model_said = MODEL_SAID.model_copy(update={"time_min": 30})
    monkeypatch.setattr(extract, "fetch_html", lambda url: "<html></html>")
    monkeypatch.setattr(extract, "scrape_jsonld", lambda html, url: scraped)
    stub = StubExtractor(result=model_said)

    recipe = extract.from_url("https://redhousespice.com/x/", stub, VOCAB)

    assert recipe.time_min == 745


def test_a_page_without_structured_data_is_low_confidence(monkeypatch):
    monkeypatch.setattr(extract, "fetch_html", lambda url: "<html></html>")
    monkeypatch.setattr(extract, "scrape_jsonld", lambda html, url: None)
    monkeypatch.setattr(extract, "readable_text", lambda html, url: "chicken and onion")

    recipe = extract.from_url("https://example.com/stew", StubExtractor(), VOCAB)

    assert recipe.high_confidence is False
    assert recipe.name == "Model title"
    assert recipe.source_text == "chicken and onion"


def test_a_page_without_structured_data_has_a_canonical_source_url(monkeypatch):
    monkeypatch.setattr(extract, "fetch_html", lambda url: "<html></html>")
    monkeypatch.setattr(extract, "scrape_jsonld", lambda html, url: None)
    monkeypatch.setattr(extract, "readable_text", lambda html, url: "chicken and onion")

    recipe = extract.from_url(
        "https://example.com/stew?ref=share#top", StubExtractor(), VOCAB
    )

    assert recipe.source_url == "https://example.com/stew"


def test_a_social_url_sends_the_caption_and_the_frames(monkeypatch):
    result = SocialResult(caption="Best noodles", thumbnail_url="https://cdn/t.jpg", frames=[b"f1"])
    monkeypatch.setattr(extract, "fetch_social", lambda url, workdir: result)
    stub = StubExtractor()

    recipe = extract.from_url("https://www.instagram.com/p/abc/", stub, VOCAB)

    assert recipe.source == "Instagram"
    assert recipe.high_confidence is False
    assert recipe.image_url == "https://cdn/t.jpg"
    assert recipe.source_text == "Best noodles"
    kinds = [type(part) for part in stub.calls[0]]
    assert kinds.count(ImagePart) == 1
    assert TextPart in kinds


def test_a_social_url_has_a_canonical_source_url(monkeypatch):
    result = SocialResult(caption="Best noodles", thumbnail_url="https://cdn/t.jpg", frames=[b"f1"])
    monkeypatch.setattr(extract, "fetch_social", lambda url, workdir: result)

    recipe = extract.from_url(
        "https://www.instagram.com/p/abc/?igsh=xyz", StubExtractor(), VOCAB
    )

    assert recipe.source_url == "https://www.instagram.com/p/abc/"


def test_a_blocked_social_url_propagates(monkeypatch):
    def blow_up(url, workdir):
        raise SocialBlocked("login required")

    monkeypatch.setattr(extract, "fetch_social", blow_up)

    with pytest.raises(SocialBlocked):
        extract.from_url("https://www.instagram.com/p/abc/", StubExtractor(), VOCAB)


def test_from_photo_sends_an_image_block_and_no_source_url():
    stub = StubExtractor()

    recipe = extract.from_photo([(b"\x89PNG", "image/png")], stub, VOCAB)

    assert recipe.source == "Photo"
    assert recipe.source_url == ""
    assert recipe.high_confidence is False
    assert isinstance(stub.calls[0][0], ImagePart)


def test_from_text_keeps_the_pasted_text_as_source_text():
    recipe = extract.from_text("200g noodles, boil them", StubExtractor(), VOCAB)

    assert recipe.source == "Text"
    assert recipe.source_text == "200g noodles, boil them"


def test_an_empty_extraction_returns_none():
    stub = StubExtractor(result=None)

    assert extract.from_text("hello", stub, VOCAB) is None
