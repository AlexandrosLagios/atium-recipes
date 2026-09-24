import httpx
from notion_client.errors import APIResponseError

from recipebot.models import Ingredient, Recipe
from recipebot.notion import NotionStore, Vocabulary
from recipebot.notion import create_user_databases, load_schema_fixture
from recipebot.users import UserRecord

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


FIXTURE = {
    "recipes": {"properties": {"Name": {"type": "title", "title": {}}}},
    "ingredients": {
        "properties": {
            "Name": {"type": "title", "title": {}},
            "Used in": {"type": "rollup", "rollup": {"relation_property_name": "Recipes", "rollup_property_name": "Name", "function": "count"}},
        }
    },
}


class FakeChildrenList:
    def __init__(self, existing_blocks):
        self.existing_blocks = existing_blocks

    def list(self, block_id, **kwargs):
        return {"results": self.existing_blocks, "has_more": False, "next_cursor": None}


class FakeDatabases:
    def __init__(self):
        self.created = []
        self._by_id = {}

    def create(self, **kwargs):
        db_id = f"db{len(self.created) + 1}"
        ds_id = f"ds{len(self.created) + 1}"
        self.created.append(kwargs)
        self._by_id[db_id] = ds_id
        return {"id": db_id, "data_sources": [{"id": ds_id}]}

    def retrieve(self, database_id):
        return {"id": database_id, "data_sources": [{"id": self._by_id[database_id]}]}


class FakeDataSourcesForSchema:
    def __init__(self, reciprocal_schema):
        self.reciprocal_schema = reciprocal_schema
        self.updated = []

    def retrieve(self, data_source_id):
        return self.reciprocal_schema

    def update(self, **kwargs):
        self.updated.append(kwargs)


class FakeSchemaClient:
    def __init__(self, existing_blocks=(), reciprocal_schema=None):
        self.blocks = type("B", (), {"children": FakeChildrenList(list(existing_blocks))})()
        self.databases = FakeDatabases()
        self.data_sources = FakeDataSourcesForSchema(reciprocal_schema or {"properties": {}})


def test_load_schema_fixture_reads_the_shipped_file():
    fixture = load_schema_fixture()

    assert "Name" in fixture["recipes"]["properties"]
    assert "Name" in fixture["ingredients"]["properties"]


def test_create_user_databases_creates_both_from_the_fixture():
    reciprocal_schema = {
        "properties": {"Recipes": {"type": "relation", "relation": {"data_source_id": "ds2"}}}
    }
    client = FakeSchemaClient(reciprocal_schema=reciprocal_schema)

    recipes_ds, ingredients_ds = create_user_databases(client, "page-1", FIXTURE)

    assert recipes_ds == "ds2"
    assert ingredients_ds == "ds1"
    titles = [call["title"][0]["text"]["content"] for call in client.databases.created]
    assert titles == ["Ingredients", "Recipes"]
    # The Recipes call carries a relation pointed at the just-created Ingredients data source.
    recipes_call = client.databases.created[1]
    assert recipes_call["initial_data_source"]["properties"]["Ingredients"]["relation"]["data_source_id"] == "ds1"


def test_create_user_databases_reuses_an_existing_pair_instead_of_duplicating():
    existing = [
        {"type": "child_database", "id": "db1", "child_database": {"title": "Ingredients"}},
        {"type": "child_database", "id": "db2", "child_database": {"title": "Recipes"}},
    ]
    reciprocal_schema = {
        "properties": {"Recipes": {"type": "relation", "relation": {"data_source_id": "ds2"}}}
    }
    client = FakeSchemaClient(existing_blocks=existing, reciprocal_schema=reciprocal_schema)
    client.databases._by_id = {"db1": "ds1", "db2": "ds2"}

    recipes_ds, ingredients_ds = create_user_databases(client, "page-1", FIXTURE)

    assert (recipes_ds, ingredients_ds) == ("ds2", "ds1")
    assert client.databases.created == []


def test_create_user_databases_rewires_ingredients_even_when_recipes_already_existed():
    """Covers the gap where a prior call created Recipes but failed before
    finishing the Ingredients wiring: a retry must still re-apply it, not
    skip it just because Recipes is now found rather than freshly created."""
    existing = [
        {"type": "child_database", "id": "db1", "child_database": {"title": "Ingredients"}},
        {"type": "child_database", "id": "db2", "child_database": {"title": "Recipes"}},
    ]
    reciprocal_schema = {
        "properties": {"Recipes": {"type": "relation", "relation": {"data_source_id": "ds2"}}}
    }
    client = FakeSchemaClient(existing_blocks=existing, reciprocal_schema=reciprocal_schema)
    client.databases._by_id = {"db1": "ds1", "db2": "ds2"}

    recipes_ds, ingredients_ds = create_user_databases(client, "page-1", FIXTURE)

    assert (recipes_ds, ingredients_ds) == ("ds2", "ds1")
    assert client.databases.created == []
    assert len(client.data_sources.updated) == 1
    update_call = client.data_sources.updated[0]
    assert update_call["data_source_id"] == "ds1"
    assert update_call["properties"]["Used in"] == FIXTURE["ingredients"]["properties"]["Used in"]


def test_from_user_builds_a_store_from_a_user_record():
    record = UserRecord(
        telegram_user_id=1,
        notion_access_token="tok-1",
        notion_refresh_token="refresh-1",
        recipes_ds="ds-r",
        ingredients_ds="ds-i",
        workspace_name="Alex's Kitchen",
        connected_at=1,
    )

    store = NotionStore.from_user(record)

    assert store.recipes_ds == "ds-r"
    assert store.ingredients_ds == "ds-i"


def test_create_user_databases_builds_a_new_pair_beside_a_trashed_one():
    trashed = [
        {"type": "child_database", "id": "old1", "in_trash": True, "child_database": {"title": "Ingredients"}},
        {"type": "child_database", "id": "old2", "in_trash": True, "child_database": {"title": "Recipes"}},
    ]
    reciprocal_schema = {
        "properties": {"Recipes": {"type": "relation", "relation": {"data_source_id": "ds2"}}}
    }
    client = FakeSchemaClient(existing_blocks=trashed, reciprocal_schema=reciprocal_schema)

    recipes_ds, ingredients_ds = create_user_databases(client, "page-1", FIXTURE)

    assert (recipes_ds, ingredients_ds) == ("ds2", "ds1")
    assert len(client.databases.created) == 2


class FakeRetrieveOnly:
    def __init__(self, missing=()):
        self.missing = set(missing)

    def retrieve(self, data_source_id):
        if data_source_id in self.missing:
            raise APIResponseError(
                code="object_not_found", status=404, message="gone", headers=httpx.Headers(), raw_body_text=""
            )
        return {"id": data_source_id}


def a_store_missing(*missing) -> NotionStore:
    client = type("C", (), {"data_sources": FakeRetrieveOnly(missing)})()
    return NotionStore(client, "ds-r", "ds-i")


def test_databases_gone_is_false_while_both_databases_answer():
    assert a_store_missing().databases_gone() is False


def test_databases_gone_is_true_when_either_database_is_missing():
    assert a_store_missing("ds-i").databases_gone() is True
