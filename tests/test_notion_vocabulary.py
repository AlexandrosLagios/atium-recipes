import httpx
from notion_client.errors import APIResponseError

from recipebot.notion import NotionStore


class FakeDataSources:
    def __init__(self):
        self.queries = []
        self.updates = []
        self.schemas = {
            "ds-recipes": {
                "properties": {
                    "Cuisine": {"type": "select", "select": {"options": [{"name": "Chinese"}]}},
                    "Meal": {
                        "type": "multi_select",
                        "multi_select": {"options": [{"name": "Side"}, {"name": "Dinner"}]},
                    },
                }
            },
            "ds-ingredients": {
                "properties": {
                    "Category": {"type": "select", "select": {"options": [{"name": "Staples"}]}}
                }
            },
        }

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
        return self.schemas[data_source_id]

    def update(self, data_source_id, properties):
        self.updates.append((data_source_id, properties))
        self.schemas[data_source_id]["properties"].update(properties)
        return self.schemas[data_source_id]


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


def test_vocabulary_adds_a_property_an_older_schema_is_missing():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.vocabulary()

    added = {
        name
        for data_source_id, properties in client.data_sources.updates
        if data_source_id == "ds-recipes"
        for name in properties
    }
    assert "Keeps (days)" in added
    assert "Missing count" not in added


def test_vocabulary_writes_no_schema_update_when_nothing_is_missing():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.vocabulary()
    client.data_sources.updates.clear()
    store.vocabulary()

    assert client.data_sources.updates == []


class RefusingDataSources(FakeDataSources):
    def update(self, data_source_id, properties):
        raise APIResponseError(
            code="validation_error",
            status=400,
            message="Keeps (days) could not be added",
            headers=httpx.Headers({}),
            raw_body_text="{}",
        )


class RefusingClient:
    def __init__(self):
        self.data_sources = RefusingDataSources()


def test_vocabulary_still_reads_when_notion_refuses_the_schema_repair():
    store = NotionStore(RefusingClient(), "ds-recipes", "ds-ingredients")

    vocab = store.vocabulary()

    assert vocab.ingredients == {"Chicken": "p1", "Cucumber": "p2"}
    assert vocab.cuisines == ["Chinese"]


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
