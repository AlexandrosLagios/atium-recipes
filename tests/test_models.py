from recipebot.models import ExtractedRecipe, Ingredient, Recipe, canonical_url


def test_canonical_url_strips_query_and_fragment():
    url = "https://redhousespice.com/overnight-pickled-vegetables/?utm_source=x#recipe"
    assert canonical_url(url) == "https://redhousespice.com/overnight-pickled-vegetables/"


def test_canonical_url_keeps_the_trailing_slash_as_given():
    assert canonical_url("https://example.com/a") == "https://example.com/a"
    assert canonical_url("https://example.com/a/") == "https://example.com/a/"


def test_recipe_source_url_is_canonical_at_construction():
    recipe = Recipe(
        name="Braise",
        cuisine="Chinese",
        meal=["Dinner"],
        difficulty="Easy",
        time_min=90,
        servings=4,
        ingredients=[],
        method=["Brown.", "Simmer."],
        source="Web",
        source_url="https://example.com/braise?utm_source=x#top",
    )

    assert recipe.source_url == "https://example.com/braise"


def test_from_extracted_carries_provenance():
    extracted = ExtractedRecipe(
        name="Overnight pickled vegetables",
        cuisine="Chinese",
        meal=["Side"],
        difficulty="Easy",
        time_min=745,
        servings=4,
        ingredients=[Ingredient(name="Cucumber", quantity="2 medium", category="Vegetables and aromatics")],
        method=["Salt the cucumber.", "Rest overnight."],
    )

    recipe = Recipe.from_extracted(
        extracted,
        source="Web",
        source_url="https://redhousespice.com/overnight-pickled-vegetables/",
        image_url="https://redhousespice.com/cover.jpg",
        source_text="raw body",
        high_confidence=True,
    )

    assert recipe.time_min == 745
    assert recipe.source == "Web"
    assert recipe.high_confidence is True
    assert recipe.ingredients[0].name == "Cucumber"
    assert recipe.ingredients[0].category == "Vegetables and aromatics"
