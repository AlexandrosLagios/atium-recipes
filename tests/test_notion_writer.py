from recipebot.models import Ingredient, Recipe
from recipebot.notion import NotionStore, Vocabulary

VOCAB = Vocabulary(
    ingredients={},
    cuisines=["Chinese"],
    meals=["Side", "Lunch", "Dinner"],
    categories=[],
)

SPEC_MEALS = ["Breakfast", "Lunch", "Dinner", "Dessert", "Snack", "Side"]
SPEC_CATEGORIES = [
    "Vegetables and aromatics",
    "Sauces and condiments",
    "Spices and seasonings",
    "Staples",
    "Protein",
    "Dairy and eggs",
]
# Cuisine is a bare select in the spec, so its options are whatever has been
# written before. This is the accumulated list that made fuzzy matching corrupt
# a new cuisine into an old neighbour.
SPEC_VOCAB = Vocabulary(
    ingredients={},
    cuisines=["Indian", "Peruvian", "American", "Japanese", "German", "Spanish", "Greek"],
    meals=SPEC_MEALS,
    categories=SPEC_CATEGORIES,
)


class FakePages:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "r1", "url": "https://notion.so/r1"}


class FakeChildren:
    def __init__(self):
        self.appended = []

    def append(self, **kwargs):
        self.appended.append(kwargs)
        return {}


class FakeBlocks:
    def __init__(self):
        self.children = FakeChildren()


class FakeClient:
    def __init__(self):
        self.pages = FakePages()
        self.blocks = FakeBlocks()


def a_recipe(**overrides) -> Recipe:
    defaults = dict(
        name="Overnight pickled vegetables",
        cuisine="Chinese",
        meal=["Side"],
        difficulty="Easy",
        time_min=745,
        servings=4,
        ingredients=[Ingredient(name="Cucumber", quantity="2 medium")],
        method=["Salt the cucumber.", "Rest overnight."],
        source="Web",
        source_url="https://redhousespice.com/x/?utm_source=a",
        image_url="https://redhousespice.com/cover.jpg",
        source_text="raw body",
    )
    return Recipe(**{**defaults, **overrides})


def test_create_recipe_writes_the_canonical_url_and_the_cover():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    url = store.create_recipe(a_recipe(), ["p2"], VOCAB)

    assert url == "https://notion.so/r1"
    call = client.pages.created[0]
    assert call["parent"] == {"type": "data_source_id", "data_source_id": "ds-recipes"}
    assert call["cover"] == {
        "type": "external",
        "external": {"url": "https://redhousespice.com/cover.jpg"},
    }
    props = call["properties"]
    assert props["Source URL"]["url"] == "https://redhousespice.com/x/"
    assert props["Time (min)"]["number"] == 745
    assert props["Meal"]["multi_select"] == [{"name": "Side"}]
    assert props["Ingredients"]["relation"] == [{"id": "p2"}]
    assert "Rating" not in props
    assert "Missing" not in props
    assert "Missing count" not in props


def test_create_recipe_omits_the_url_and_the_cover_when_absent():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.create_recipe(a_recipe(source="Photo", source_url="", image_url=""), [], VOCAB)

    call = client.pages.created[0]
    assert "Source URL" not in call["properties"]
    assert "cover" not in call


def test_body_carries_the_three_headings_and_a_collapsed_source_toggle():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.create_recipe(a_recipe(), ["p2"], VOCAB)

    blocks = client.pages.created[0]["children"]
    headings = [
        b["heading_2"]["rich_text"][0]["text"]["content"]
        for b in blocks
        if b["type"] == "heading_2"
    ]
    assert headings == ["Ingredients", "Method", "Notes"]

    bullets = [b for b in blocks if b["type"] == "bulleted_list_item"]
    assert bullets[0]["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "2 medium Cucumber"

    numbered = [b for b in blocks if b["type"] == "numbered_list_item"]
    assert len(numbered) == 2

    toggle = next(b for b in blocks if b["type"] == "toggle")
    assert toggle["toggle"]["rich_text"][0]["text"]["content"] == "Source text"
    assert toggle["toggle"]["children"][0]["paragraph"]["rich_text"][0]["text"]["content"] == "raw body"


def test_more_than_a_hundred_blocks_are_appended_in_chunks():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.create_recipe(a_recipe(method=[f"Step {i}." for i in range(150)]), [], VOCAB)

    assert len(client.pages.created[0]["children"]) == 100
    assert client.blocks.children.appended[0]["block_id"] == "r1"
    assert len(client.blocks.children.appended[0]["children"]) <= 100


def test_a_comma_in_the_cuisine_is_cleaned_to_the_text_before_it():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.create_recipe(a_recipe(cuisine="Chinese, Asian"), [], VOCAB)

    props = client.pages.created[0]["properties"]
    assert props["Cuisine"]["select"]["name"] == "Chinese"


def test_a_comma_in_a_meal_is_cleaned_to_the_text_before_it():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.create_recipe(a_recipe(meal=["Lunch, Dinner"]), [], VOCAB)

    props = client.pages.created[0]["properties"]
    assert props["Meal"]["multi_select"] == [{"name": "Lunch"}]


def test_a_blank_cuisine_omits_the_property_instead_of_writing_an_empty_option():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.create_recipe(a_recipe(cuisine=""), [], VOCAB)

    assert "Cuisine" not in client.pages.created[0]["properties"]


def test_duplicate_cleaned_meal_names_are_deduplicated():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.create_recipe(a_recipe(meal=["Lunch, Dinner", "Lunch"]), [], VOCAB)

    props = client.pages.created[0]["properties"]
    assert props["Meal"]["multi_select"] == [{"name": "Lunch"}]


def test_a_blank_meal_entry_is_dropped_rather_than_written_empty():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.create_recipe(a_recipe(meal=["", "Dinner"]), [], VOCAB)

    props = client.pages.created[0]["properties"]
    assert props["Meal"]["multi_select"] == [{"name": "Dinner"}]


def test_a_drifted_cuisine_snaps_to_the_known_spelling():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")
    vocab = Vocabulary(ingredients={}, cuisines=["Greek"], meals=[], categories=[])

    store.create_recipe(a_recipe(cuisine="greek "), [], vocab)

    props = client.pages.created[0]["properties"]
    assert props["Cuisine"]["select"]["name"] == "Greek"


def test_a_drifted_meal_snaps_to_the_known_spelling():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")
    vocab = Vocabulary(ingredients={}, cuisines=[], meals=["Dinner"], categories=[])

    store.create_recipe(a_recipe(meal=["dinner "]), [], vocab)

    props = client.pages.created[0]["properties"]
    assert props["Meal"]["multi_select"] == [{"name": "Dinner"}]


def test_a_genuinely_new_cuisine_with_no_close_option_is_kept_as_written():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")
    vocab = Vocabulary(ingredients={}, cuisines=["Chinese", "Greek"], meals=[], categories=[])

    store.create_recipe(a_recipe(cuisine="Ethiopian"), [], vocab)

    props = client.pages.created[0]["properties"]
    assert props["Cuisine"]["select"]["name"] == "Ethiopian"


def test_a_genuinely_new_cuisine_with_a_comma_is_kept_up_to_the_comma():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")
    vocab = Vocabulary(ingredients={}, cuisines=["Chinese", "Greek"], meals=[], categories=[])

    store.create_recipe(a_recipe(cuisine="Sichuan, Chinese"), [], vocab)

    props = client.pages.created[0]["properties"]
    assert props["Cuisine"]["select"]["name"] == "Sichuan"


def test_an_unknown_meal_entry_is_dropped_since_meal_is_a_fixed_vocabulary():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")
    vocab = Vocabulary(ingredients={}, cuisines=[], meals=["Dinner"], categories=[])

    store.create_recipe(a_recipe(meal=["Brunch"]), [], vocab)

    props = client.pages.created[0]["properties"]
    assert props["Meal"]["multi_select"] == []


def test_brunch_is_dropped_rather_than_filed_as_lunch():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.create_recipe(a_recipe(meal=["Brunch"]), [], SPEC_VOCAB)

    props = client.pages.created[0]["properties"]
    assert props["Meal"]["multi_select"] == []


def test_an_unknown_cuisine_is_written_as_given_not_snapped_to_a_neighbour():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.create_recipe(a_recipe(cuisine="Indonesian"), [], SPEC_VOCAB)

    props = client.pages.created[0]["properties"]
    assert props["Cuisine"]["select"]["name"] == "Indonesian"


def test_no_accumulated_cuisine_is_snapped_onto_a_close_existing_neighbour():
    for written, wrong in [
        ("Persian", "Peruvian"),
        ("Jamaican", "American"),
        ("Javanese", "Japanese"),
        ("Georgian", "German"),
        ("Danish", "Spanish"),
    ]:
        client = FakeClient()
        store = NotionStore(client, "ds-recipes", "ds-ingredients")

        store.create_recipe(a_recipe(cuisine=written), [], SPEC_VOCAB)

        name = client.pages.created[0]["properties"]["Cuisine"]["select"]["name"]
        assert name == written, f"{written} was written as {wrong}"


def test_every_written_meal_and_cuisine_name_is_free_of_commas():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.create_recipe(
        a_recipe(cuisine="Sichuan, Chinese", meal=["Lunch, Dinner", "Side"]),
        [],
        SPEC_VOCAB,
    )

    props = client.pages.created[0]["properties"]
    assert props["Cuisine"]["select"]["name"] == "Sichuan"
    assert props["Meal"]["multi_select"] == [{"name": "Lunch"}, {"name": "Side"}]


class RaisingChildren:
    def append(self, **kwargs):
        raise RuntimeError("Notion append failed")


class RaisingBlocks:
    def __init__(self):
        self.children = RaisingChildren()


def test_an_append_failure_on_a_long_body_still_returns_the_page_url():
    client = FakeClient()
    client.blocks = RaisingBlocks()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    url = store.create_recipe(a_recipe(method=[f"Step {i}." for i in range(150)]), [], VOCAB)

    assert url == "https://notion.so/r1"


def test_create_recipe_sets_the_emoji_as_the_page_icon():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.create_recipe(a_recipe(emoji="🥘"), ["p2"], VOCAB)

    assert client.pages.created[0]["icon"] == {"type": "emoji", "emoji": "🥘"}


def test_create_recipe_omits_the_icon_when_the_model_gave_no_emoji():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.create_recipe(a_recipe(emoji=""), ["p2"], VOCAB)

    assert "icon" not in client.pages.created[0]
