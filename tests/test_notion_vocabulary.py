from recipebot.notion import NotionStore, load_schema_fixture


class FakeDataSources:
    def __init__(self):
        self.queries = []
        self.updates = []

    def update(self, data_source_id, properties):
        self.updates.append((data_source_id, properties))
        return {}

    def query(self, **kwargs):
        self.queries.append(kwargs)
        if kwargs["data_source_id"] != "ds-ingredients":
            raise AssertionError("vocabulary must read the Ingredients data source")
        if kwargs.get("start_cursor") is None:
            return {
                "results": [
                    {"id": "p1", "properties": {"Name": {"title": [{"plain_text": "Chicken"}]}}}
                ],
                "has_more": True,
                "next_cursor": "c1",
            }
        return {
            "results": [
                {"id": "p2", "properties": {"Name": {"title": [{"plain_text": "Cucumber"}]}}}
            ],
            "has_more": False,
            "next_cursor": None,
        }

    def retrieve(self, data_source_id):
        if data_source_id == "ds-recipes":
            return {
                "properties": {
                    "Cuisine": {"type": "select", "select": {"options": [{"name": "Chinese"}]}},
                    "Meal": {
                        "type": "multi_select",
                        "multi_select": {"options": [{"name": "Side"}, {"name": "Dinner"}]},
                    },
                }
            }
        return {
            "properties": {
                "Category": {"type": "select", "select": {"options": [{"name": "Staples"}]}}
            }
        }


class FakeClient:
    def __init__(self):
        self.data_sources = FakeDataSources()


def test_vocabulary_pages_through_every_ingredient():
    store = NotionStore(FakeClient(), "ds-recipes", "ds-ingredients")

    vocab = store.vocabulary()

    assert vocab.ingredients == {"Chicken": "p1", "Cucumber": "p2"}
    assert vocab.cuisines == ["Chinese"]
    assert vocab.meals == ["Side", "Dinner"]
    assert vocab.categories == ["Staples"]


def test_vocabulary_adds_the_fixture_properties_the_user_is_missing():
    """A user who connected before a property existed never gets it otherwise:
    create_user_databases only runs once, at the OAuth callback."""
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.vocabulary()

    added = {
        name: ds
        for ds, properties in client.data_sources.updates
        for name in properties
    }
    assert added["Keeps (days)"] == "ds-recipes"
    assert added["In pantry"] == "ds-ingredients"
    assert "Cuisine" not in added, "a property the user already has must never be re-sent"
    assert "Missing" not in added, "Notion refuses the relation-traversing formula"
    assert "Name" not in added, "a data source can only hold one title property"


class UpToDateDataSources(FakeDataSources):
    def retrieve(self, data_source_id):
        key = "recipes" if data_source_id == "ds-recipes" else "ingredients"
        return {"properties": load_schema_fixture()[key]["properties"]}


def test_vocabulary_updates_nothing_when_the_user_is_already_current():
    client = FakeClient()
    client.data_sources = UpToDateDataSources()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.vocabulary()

    assert client.data_sources.updates == []


class MalformedDataSources:
    def __init__(self):
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        if len(self.queries) > 1:
            raise AssertionError("looped: a falsy cursor must stop pagination")
        return {
            "results": [{"id": "p1", "properties": {"Name": {"title": [{"plain_text": "Chicken"}]}}}],
            "has_more": True,
        }


class MalformedClient:
    def __init__(self):
        self.data_sources = MalformedDataSources()


def test_all_pages_stops_when_has_more_is_true_but_the_cursor_is_falsy():
    client = MalformedClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    pages = store._all_pages("ds-ingredients")

    assert len(client.data_sources.queries) == 1
    assert pages == [{"id": "p1", "properties": {"Name": {"title": [{"plain_text": "Chicken"}]}}}]
