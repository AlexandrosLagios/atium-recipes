from pathlib import Path

from recipebot.scrape import ScrapeResult, readable_text, scrape_jsonld

FIXTURES = Path(__file__).parent / "fixtures"
JSONLD = (FIXTURES / "jsonld_recipe.html").read_text()
PLAIN = (FIXTURES / "plain_article.html").read_text()
URL = "https://redhousespice.com/overnight-pickled-vegetables/"


def test_scrape_jsonld_reads_the_structured_fields():
    result = scrape_jsonld(JSONLD, URL)

    assert result.name == "Overnight pickled vegetables"
    assert result.time_min == 745
    assert result.servings == 4
    assert result.image_url == "https://example.com/cover.jpg"
    assert len(result.ingredients) == 2
    assert result.method == ["Salt the cucumber.", "Rest overnight."]


def test_scrape_jsonld_returns_none_without_structured_data():
    assert scrape_jsonld(PLAIN, "https://example.com/stew") is None


def test_as_prompt_carries_every_field():
    prompt = ScrapeResult(
        name="Pickles",
        time_min=745,
        servings=4,
        ingredients=["2 medium cucumbers"],
        method=["Salt."],
        cuisine="Chinese",
    ).as_prompt()

    assert "Pickles" in prompt
    assert "2 medium cucumbers" in prompt
    assert "745" in prompt


def test_readable_text_returns_the_article_body():
    text = readable_text(PLAIN, "https://example.com/stew")

    assert "chicken" in text.lower()
    assert "simmer" in text.lower()
