from recipebot.models import Ingredient, Recipe
from recipebot.notion import NotionStore, Vocabulary

VOCAB = Vocabulary(
    ingredients={"Chicken": "p1", "Soy sauce": "p3"},
    cuisines=["Chinese"],
    meals=["Dinner"],
    categories=["Staples", "Protein"],
)


class FakePages:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return {"id": f"page{len(self.created)}", "url": f"https://notion.so/page{len(self.created)}"}


class FakeChildren:
    def append(self, **kwargs):
        return {}


class FakeBlocks:
    def __init__(self):
        self.children = FakeChildren()


class FakeDataSources:
    def __init__(self, existing_url=None):
        self.existing_url = existing_url
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        if self.existing_url:
            return {
                "results": [{"id": "existing", "url": self.existing_url}],
                "has_more": False,
                "next_cursor": None,
            }
        return {"results": [], "has_more": False, "next_cursor": None}


class FakeClient:
    def __init__(self, existing_url=None):
        self.pages = FakePages()
        self.blocks = FakeBlocks()
        self.data_sources = FakeDataSources(existing_url)


def a_recipe(ingredients) -> Recipe:
    return Recipe(
        name="Braise",
        cuisine="Chinese",
        meal=["Dinner"],
        difficulty="Easy",
        time_min=90,
        servings=4,
        ingredients=ingredients,
        method=["Brown.", "Simmer."],
        source="Web",
        source_url="https://example.com/braise",
    )


def test_an_existing_ingredient_is_linked_not_created():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.save_recipe(a_recipe([Ingredient(name="chicken", category="Protein")]), VOCAB)

    assert len(client.pages.created) == 1
    assert client.pages.created[0]["properties"]["Ingredients"]["relation"] == [{"id": "p1"}]


def test_a_new_ingredient_is_created_first_then_linked():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.save_recipe(a_recipe([Ingredient(name="Star anise", category="Staples")]), VOCAB)

    assert len(client.pages.created) == 2
    ingredient_call, recipe_call = client.pages.created
    assert ingredient_call["properties"]["Name"]["title"][0]["text"]["content"] == "Star anise"
    assert ingredient_call["properties"]["In pantry"]["checkbox"] is False
    assert recipe_call["properties"]["Ingredients"]["relation"] == [{"id": "page1"}]


def test_a_merge_decision_links_the_existing_row():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.save_recipe(
        a_recipe([Ingredient(name="Soy Sauces", category="Staples")]),
        VOCAB,
        merges={"Soy Sauces": "Soy sauce"},
    )

    assert len(client.pages.created) == 1
    assert client.pages.created[0]["properties"]["Ingredients"]["relation"] == [{"id": "p3"}]


def test_a_repeated_ingredient_is_linked_once():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.save_recipe(
        a_recipe([Ingredient(name="Chicken"), Ingredient(name="chicken")]), VOCAB
    )

    assert client.pages.created[0]["properties"]["Ingredients"]["relation"] == [{"id": "p1"}]


def test_a_repeated_new_ingredient_creates_one_row_and_links_it_once():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.save_recipe(
        a_recipe(
            [
                Ingredient(name="Star anise", category="Staples"),
                Ingredient(name="star anise", category="Staples"),
            ]
        ),
        VOCAB,
    )

    assert len(client.pages.created) == 2
    ingredient_call, recipe_call = client.pages.created
    assert ingredient_call["properties"]["Name"]["title"][0]["text"]["content"] == "Star anise"
    assert recipe_call["properties"]["Ingredients"]["relation"] == [{"id": "page1"}]


def test_save_recipe_returns_the_new_url_and_created_true_for_a_fresh_source():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    url, created = store.save_recipe(a_recipe([Ingredient(name="Chicken")]), VOCAB)

    assert created is True
    assert url == "https://notion.so/page1"


def test_save_recipe_dedupes_by_source_url_and_creates_nothing():
    client = FakeClient(existing_url="https://notion.so/existing")
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    url, created = store.save_recipe(
        a_recipe([Ingredient(name="Star anise", category="Staples")]), VOCAB
    )

    assert created is False
    assert url == "https://notion.so/existing"
    assert client.pages.created == []
