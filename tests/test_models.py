from recipebot.models import ExtractedRecipe, Ingredient, Recipe, canonical_url

EXTRACTED = {
    "name": "Overnight pickled vegetables",
    "cuisine": "Chinese",
    "meal": ["Side"],
    "difficulty": "Easy",
    "time_min": 745,
    "servings": 4,
    "ingredients": [Ingredient(name="Cucumber", quantity="2 medium")],
    "method": ["Salt the cucumber."],
}


def test_canonical_url_strips_query_and_fragment():
    url = "https://redhousespice.com/overnight-pickled-vegetables/?utm_source=x#recipe"
    assert canonical_url(url) == "https://redhousespice.com/overnight-pickled-vegetables/"


def test_canonical_url_keeps_the_trailing_slash_as_given():
    assert canonical_url("https://example.com/a") == "https://example.com/a"
    assert canonical_url("https://example.com/a/") == "https://example.com/a/"


def test_two_youtube_videos_do_not_share_one_canonical_url():
    first = canonical_url("https://www.youtube.com/watch?v=AAAAAAAAAAA")
    second = canonical_url("https://www.youtube.com/watch?v=BBBBBBBBBBB")

    assert first == "https://www.youtube.com/watch?v=AAAAAAAAAAA"
    assert first != second


def test_youtube_tracking_parameters_are_stripped_but_the_video_id_stays():
    tracked = "https://www.youtube.com/watch?v=AAAAAAAAAAA&list=PL1&t=42s&si=xyz#t=10"

    assert canonical_url(tracked) == canonical_url("https://www.youtube.com/watch?v=AAAAAAAAAAA")


def test_a_youtu_be_short_url_keeps_the_id_in_the_path():
    assert canonical_url("https://youtu.be/AAAAAAAAAAA?si=xyz") == "https://youtu.be/AAAAAAAAAAA"


def test_a_youtube_watch_url_without_a_video_id_keeps_no_query():
    assert canonical_url("https://www.youtube.com/watch?list=PL1") == "https://www.youtube.com/watch"


def test_a_non_youtube_query_string_is_still_stripped_entirely():
    assert canonical_url("https://example.com/r?v=AAAAAAAAAAA") == "https://example.com/r"


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


def test_assigning_a_raw_source_url_is_canonicalised_too():
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
    )

    recipe.source_url = "https://example.com/braise?utm_source=x#top"

    assert recipe.source_url == "https://example.com/braise"


def test_the_scraper_may_still_overwrite_the_fields_extract_mutates():
    recipe = Recipe(
        name="Braise",
        cuisine="Chinese",
        meal=["Dinner"],
        difficulty="Easy",
        time_min=90,
        servings=4,
        ingredients=[],
        method=["Brown."],
        source="Web",
    )

    recipe.name = "Red braised pork"
    recipe.time_min = max(120, recipe.time_min)
    recipe.servings = 6
    recipe.method = ["Brown.", "Simmer."]

    assert (recipe.name, recipe.time_min, recipe.servings) == ("Red braised pork", 120, 6)
    assert recipe.method == ["Brown.", "Simmer."]


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
    )

    assert recipe.time_min == 745
    assert recipe.source == "Web"
    assert recipe.ingredients[0].name == "Cucumber"
    assert recipe.ingredients[0].category == "Vegetables and aromatics"


def test_emoji_survives_a_plain_symbol():
    assert ExtractedRecipe(**{**EXTRACTED, "emoji": "🥘"}).emoji == "🥘"


def test_emoji_keeps_a_whole_joined_sequence():
    assert ExtractedRecipe(**{**EXTRACTED, "emoji": "👨‍🍳"}).emoji == "👨‍🍳"


def test_emoji_keeps_a_whole_flag():
    assert ExtractedRecipe(**{**EXTRACTED, "emoji": "🇬🇷"}).emoji == "🇬🇷"


def test_emoji_keeps_only_the_first_of_several():
    assert ExtractedRecipe(**{**EXTRACTED, "emoji": "🥘🍝🍅"}).emoji == "🥘"


def test_emoji_drops_a_word_the_model_returned_instead():
    assert ExtractedRecipe(**{**EXTRACTED, "emoji": "lasagne"}).emoji == ""
    assert ExtractedRecipe(**{**EXTRACTED, "emoji": "  "}).emoji == ""


def test_emoji_defaults_to_empty():
    assert ExtractedRecipe(**EXTRACTED).emoji == ""
