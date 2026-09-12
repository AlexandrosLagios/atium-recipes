from recipebot.models import Ingredient
from recipebot.notion import NotionStore, Vocabulary, reconcile_ingredients

VOCAB = Vocabulary(
    ingredients={"Chicken": "p1", "Cucumber": "p2", "Soy sauce": "p3"},
    cuisines=["Chinese"],
    meals=["Side"],
    categories=["Staples", "Vegetables and aromatics", "Sauces and condiments"],
)


def test_exact_match_is_case_insensitive():
    plan = reconcile_ingredients(VOCAB, [Ingredient(name="chicken")])

    assert plan.existing == {"chicken": "p1"}
    assert plan.new == []
    assert plan.near == {}


def test_a_close_name_is_reported_as_near_not_created():
    plan = reconcile_ingredients(VOCAB, [Ingredient(name="Soy Sauces")])

    assert plan.existing == {}
    assert plan.new == []
    assert plan.near == {"Soy Sauces": "Soy sauce"}


def test_a_genuinely_new_name_is_new():
    plan = reconcile_ingredients(VOCAB, [Ingredient(name="Anthotyro (Greek whey cheese)")])

    assert plan.new == ["Anthotyro (Greek whey cheese)"]
    assert plan.near == {}


class FakePages:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "new-page", "url": "https://notion.so/new-page"}


class FakeClient:
    def __init__(self):
        self.pages = FakePages()


def test_create_ingredient_leaves_in_pantry_unticked_and_snaps_the_category():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    page_id = store.create_ingredient("Anthotyro", "Dairy, and eggs", VOCAB.categories)

    assert page_id == "new-page"
    props = client.pages.created[0]["properties"]
    assert props["In pantry"]["checkbox"] is False
    assert props["Category"]["select"]["name"] == "Staples"
    assert client.pages.created[0]["parent"] == {
        "type": "data_source_id",
        "data_source_id": "ds-ingredients",
    }
