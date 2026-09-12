from recipebot.notion import NotionStore


class FakeDataSources:
    def __init__(self):
        self.queries = []

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
