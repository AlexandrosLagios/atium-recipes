"""Notion hands a relation-traversing formula back out on read but refuses to
write one in, so one unsettable property must not cost the user the rest of
the schema, or the whole connect."""

import httpx
import pytest
from notion_client.errors import APIResponseError

from recipebot.notion import create_user_databases

FIXTURE = {
    "recipes": {
        "properties": {
            "Name": {"type": "title", "title": {}},
            "Missing count": {
                "type": "rollup",
                "rollup": {
                    "relation_property_name": "Ingredients",
                    "rollup_property_name": "In pantry",
                    "function": "unchecked",
                },
            },
            "Missing": {
                "type": "formula",
                "formula": {
                    "expression": 'prop("Ingredients").map(current.prop("Name")).join(", ")'
                },
            },
        }
    },
    "ingredients": {
        "properties": {
            "Name": {"type": "title", "title": {}},
            "In pantry": {"type": "checkbox", "checkbox": {}},
            "Used in": {
                "type": "rollup",
                "rollup": {
                    "relation_property_name": "Recipes",
                    "rollup_property_name": "Name",
                    "function": "count",
                },
            },
        }
    },
}


def a_formula_error():
    return APIResponseError(
        code="validation_error",
        status=400,
        message="Type error with formula",
        headers=httpx.Headers({}),
        raw_body_text="{}",
    )


class FakeChildrenList:
    def list(self, block_id, **kwargs):
        return {"results": [], "has_more": False, "next_cursor": None}


class FakeDatabases:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        n = len(self.created)
        return {"id": f"db{n}", "data_sources": [{"id": f"ds{n}"}]}


class FakeDataSources:
    """Rejects a formula property the way Notion rejects a relation-traversing
    one, and records everything else it is asked to apply."""

    def __init__(self):
        self.applied = []
        self.rejected = []

    def retrieve(self, data_source_id):
        return {
            "properties": {"Recipes": {"type": "relation", "relation": {"data_source_id": "ds2"}}}
        }

    def update(self, **kwargs):
        for name, config in kwargs["properties"].items():
            if isinstance(config, dict) and config.get("type") == "formula":
                self.rejected.append(name)
                raise a_formula_error()
        self.applied.append(kwargs)


class FakeClient:
    def __init__(self):
        self.blocks = type("B", (), {"children": FakeChildrenList()})()
        self.databases = FakeDatabases()
        self.data_sources = FakeDataSources()


def test_an_unsettable_formula_does_not_abort_the_connect():
    client = FakeClient()

    recipes_ds, ingredients_ds = create_user_databases(client, "page-1", FIXTURE)

    assert (recipes_ds, ingredients_ds) == ("ds2", "ds1")
    assert client.data_sources.rejected == ["Missing"]


def test_the_properties_notion_accepts_are_still_applied():
    client = FakeClient()

    create_user_databases(client, "page-1", FIXTURE)

    applied = [name for call in client.data_sources.applied for name in call["properties"]]
    assert "Used in" in applied
    assert "Missing count" in applied


def test_each_computed_property_is_applied_on_its_own():
    client = FakeClient()

    create_user_databases(client, "page-1", FIXTURE)

    for call in client.data_sources.applied:
        assert len(call["properties"]) == 1


def test_a_failure_that_is_not_an_api_error_still_propagates():
    class ExplodingDataSources(FakeDataSources):
        def update(self, **kwargs):
            raise RuntimeError("something else entirely")

    client = FakeClient()
    client.data_sources = ExplodingDataSources()

    with pytest.raises(RuntimeError):
        create_user_databases(client, "page-1", FIXTURE)
