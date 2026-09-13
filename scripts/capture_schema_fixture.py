"""Snapshot the maintainer's live Recipes/Ingredients schema into
src/recipebot/notion_schema.json. Run by hand, locally, with the real
NOTION_TOKEN/NOTION_RECIPES_DS/NOTION_INGREDIENTS_DS in the environment.
Re-run whenever the maintainer's own schema changes."""
import json
import os
from pathlib import Path

from notion_client import Client

OUTPUT_PATH = Path(__file__).parent.parent / "src" / "recipebot" / "notion_schema.json"


def _drop_ids(value):
    if isinstance(value, dict):
        return {
            key: _drop_ids(val)
            for key, val in value.items()
            if key != "id" and not key.endswith("_id")
        }
    if isinstance(value, list):
        return [_drop_ids(item) for item in value]
    return value


def _strip(properties: dict) -> dict:
    # Relations are wired explicitly when a new user's pair of databases is
    # created (the target data source does not exist yet), so a relation
    # property from the live schema is dropped rather than captured.
    return {
        name: _drop_ids(config)
        for name, config in properties.items()
        if config.get("type") != "relation"
    }


def _assert_no_ids(value, path=""):
    if isinstance(value, dict):
        for key, val in value.items():
            if key == "id" or key.endswith("_id"):
                raise AssertionError(f"found an id-shaped key at {path}.{key}")
            _assert_no_ids(val, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_ids(item, f"{path}[{index}]")


def main() -> None:
    client = Client(auth=os.environ["NOTION_TOKEN"])
    recipes = client.data_sources.retrieve(data_source_id=os.environ["NOTION_RECIPES_DS"])
    ingredients = client.data_sources.retrieve(
        data_source_id=os.environ["NOTION_INGREDIENTS_DS"]
    )
    fixture = {
        "recipes": {"properties": _strip(recipes["properties"])},
        "ingredients": {"properties": _strip(ingredients["properties"])},
    }
    _assert_no_ids(fixture)
    OUTPUT_PATH.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
