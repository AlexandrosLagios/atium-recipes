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


class FakeClient:
    def __init__(self):
        self.pages = FakePages()
        self.blocks = FakeBlocks()


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
