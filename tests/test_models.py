from recipebot.models import ExtractedRecipe, Ingredient, Recipe, canonical_url


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
