from recipebot.notion import NotionStore


class FakeDataSources:
    def __init__(self, results):
        self.results = results
        self.last_filter = None

    def query(self, **kwargs):
        self.last_filter = kwargs.get("filter")
        return {"results": self.results, "has_more": False, "next_cursor": None}


class FakeClient:
    def __init__(self, results):
        self.data_sources = FakeDataSources(results)


def test_find_by_url_strips_the_query_string_before_querying():
    client = FakeClient([])
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    assert store.find_by_url("https://redhousespice.com/x/?utm_source=a") is None
    assert client.data_sources.last_filter == {
        "property": "Source URL",
        "url": {"equals": "https://redhousespice.com/x/"},
    }


def test_find_by_url_returns_the_existing_page_url():
    client = FakeClient([{"id": "p9", "url": "https://notion.so/p9"}])
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    assert store.find_by_url("https://redhousespice.com/x/") == "https://notion.so/p9"


def test_find_by_url_returns_none_for_an_empty_url():
    client = FakeClient([{"id": "p9", "url": "https://notion.so/p9"}])
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    assert store.find_by_url("") is None
    assert client.data_sources.last_filter is None
