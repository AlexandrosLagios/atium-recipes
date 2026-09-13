import json
import logging
from difflib import get_close_matches
from itertools import batched
from pathlib import Path

from notion_client import Client
from pydantic import BaseModel

from .models import Ingredient, Recipe, canonical_url

log = logging.getLogger(__name__)

SCHEMA_FIXTURE_PATH = Path(__file__).parent / "notion_schema.json"


def load_schema_fixture() -> dict:
    return json.loads(SCHEMA_FIXTURE_PATH.read_text())


class Vocabulary(BaseModel):
    ingredients: dict[str, str] = {}
    cuisines: list[str] = []
    meals: list[str] = []
    categories: list[str] = []


class IngredientPlan(BaseModel):
    existing: dict[str, str] = {}
    new: list[str] = []
    near: dict[str, str] = {}


# ponytail: the exact-match lookup above already folds case, so difflib only
# ever sees lowercase input. It catches plurals and typos, not synonyms
# ("aubergine" against "eggplant"). Swap in an embedding lookup only if the
# user reports real duplicates slipping through.
def reconcile_ingredients(vocab: Vocabulary, ingredients: list[Ingredient]) -> IngredientPlan:
    by_lower = {
        name.lower(): (name, page_id) for name, page_id in vocab.ingredients.items()
    }
    plan = IngredientPlan()
    for item in ingredients:
        key = item.name.strip().lower()
        if not key:
            continue
        if key in by_lower:
            plan.existing[item.name] = by_lower[key][1]
            continue
        close = get_close_matches(key, list(by_lower), n=1, cutoff=0.85)
        if close:
            plan.near[item.name] = by_lower[close[0]][0]
        else:
            plan.new.append(item.name)
    return plan


def _snap_category(category: str, categories: list[str]) -> str:
    candidate = category.strip()
    for known in categories:
        if candidate.lower() == known.lower():
            return known
    close = get_close_matches(candidate.lower(), [c.lower() for c in categories], n=1, cutoff=0.7)
    if close:
        return next(c for c in categories if c.lower() == close[0])
    return "Staples"


# Notion rejects a select option name that contains a comma, and the model
# regularly answers "Sichuan, Chinese", so keep only the text before the comma.
def _clean_option_name(name: str) -> str:
    return name.split(",")[0].strip()


def _known_spelling(value: str, known: list[str]) -> str | None:
    return {name.lower(): name for name in known}.get(value.lower())


# Cuisine is an open vocabulary, so a value that matches nothing is a new
# cuisine rather than a misspelling of an old one. Fold case against the known
# options, never guess: "Indonesian" is not a drifted "Indian".
def _cuisine_option(value: str, known: list[str]) -> str:
    cleaned = _clean_option_name(value)
    return _known_spelling(cleaned, known) or cleaned


# Meal is the fixed vocabulary in the spec, so an entry that matches no known
# option is dropped rather than snapped onto its nearest neighbour.
def _meal_options(names: list[str], known: list[str]) -> list[str]:
    matched = (_known_spelling(_clean_option_name(name), known) for name in names)
    return list(dict.fromkeys(name for name in matched if name))


def _title_of(page: dict) -> str:
    spans = page["properties"]["Name"]["title"]
    return "".join(span["plain_text"] for span in spans).strip()


def _options(schema: dict, prop: str) -> list[str]:
    entry = schema["properties"].get(prop)
    if not entry:
        return []
    return [option["name"] for option in entry[entry["type"]]["options"]]


RICH_TEXT_LIMIT = 2000
CHILDREN_LIMIT = 100
# ponytail: a Notion toggle's own children are not chunked, so the source text is
# capped at 90 paragraphs (180k characters). Chunk the toggle too if a real
# transcript ever hits the cap.
SOURCE_TEXT_BLOCK_LIMIT = 90


def _rt(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": text[:RICH_TEXT_LIMIT]}}]


def _block(kind: str, text: str, **extra) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": _rt(text), **extra}}


def _paragraphs(text: str) -> list[dict]:
    chunks = [
        text[i : i + RICH_TEXT_LIMIT] for i in range(0, len(text), RICH_TEXT_LIMIT)
    ] or [""]
    return [_block("paragraph", chunk) for chunk in chunks[:SOURCE_TEXT_BLOCK_LIMIT]]


def _body_blocks(recipe: Recipe) -> list[dict]:
    blocks = [_block("heading_2", "Ingredients")]
    for item in recipe.ingredients:
        line = f"{item.quantity} {item.name}".strip()
        blocks.append(_block("bulleted_list_item", line))
    blocks.append(_block("heading_2", "Method"))
    blocks.extend(_block("numbered_list_item", step) for step in recipe.method)
    blocks.append(_block("heading_2", "Notes"))
    blocks.append(_block("toggle", "Source text", children=_paragraphs(recipe.source_text)))
    return blocks


def _find_existing_data_source(client, parent_page_id: str, title: str) -> str | None:
    cursor = None
    while True:
        kwargs = {"block_id": parent_page_id}
        if cursor:
            kwargs["start_cursor"] = cursor
        page = client.blocks.children.list(**kwargs)
        for block in page["results"]:
            if block.get("type") == "child_database" and block["child_database"]["title"] == title:
                db = client.databases.retrieve(database_id=block["id"])
                return db["data_sources"][0]["id"]
        cursor = page.get("next_cursor")
        if not page.get("has_more") or not cursor:
            return None


def _create_data_source(client, parent_page_id: str, title: str, properties: dict) -> str:
    db = client.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": title}}],
        properties=properties,
    )
    return db["data_sources"][0]["id"]


def _reciprocal_relation_property(client, ingredients_ds: str, recipes_ds: str) -> str:
    schema = client.data_sources.retrieve(data_source_id=ingredients_ds)
    for name, config in schema["properties"].items():
        if config.get("type") == "relation" and config["relation"].get("data_source_id") == recipes_ds:
            return name
    raise RuntimeError("Notion did not create the reciprocal relation on Ingredients")


def create_user_databases(client, parent_page_id: str, fixture: dict) -> tuple[str, str]:
    """Create this user's Recipes/Ingredients pair under the page they
    shared during OAuth, matching the maintainer's schema fixture. Reuses
    an existing pair instead of duplicating it, so a retry after a partial
    failure is safe."""
    ingredients_ds = _find_existing_data_source(
        client, parent_page_id, "Ingredients"
    ) or _create_data_source(client, parent_page_id, "Ingredients", fixture["ingredients"]["properties"])

    recipes_ds = _find_existing_data_source(client, parent_page_id, "Recipes")
    if recipes_ds is None:
        properties = dict(fixture["recipes"]["properties"])
        properties["Ingredients"] = {
            "type": "relation",
            "relation": {"data_source_id": ingredients_ds, "type": "dual_property", "dual_property": {}},
        }
        recipes_ds = _create_data_source(client, parent_page_id, "Recipes", properties)

        reciprocal = _reciprocal_relation_property(client, ingredients_ds, recipes_ds)
        updates = {"Used in": fixture["ingredients"]["properties"]["Used in"]}
        if reciprocal != "Recipes":
            updates[reciprocal] = {"name": "Recipes"}
        client.data_sources.update(data_source_id=ingredients_ds, properties=updates)

    return recipes_ds, ingredients_ds


class NotionStore:
    def __init__(self, client, recipes_ds: str, ingredients_ds: str):
        self.client = client
        self.recipes_ds = recipes_ds
        self.ingredients_ds = ingredients_ds

    @classmethod
    def from_user(cls, record) -> "NotionStore":
        return cls(Client(auth=record.notion_access_token), record.recipes_ds, record.ingredients_ds)

    def _all_pages(self, data_source_id: str, **kwargs) -> list[dict]:
        pages, cursor = [], None
        while True:
            if cursor:
                kwargs["start_cursor"] = cursor
            page = self.client.data_sources.query(data_source_id=data_source_id, **kwargs)
            pages.extend(page["results"])
            cursor = page.get("next_cursor")
            if not page.get("has_more") or not cursor:
                return pages

    def vocabulary(self) -> Vocabulary:
        ingredients = {
            _title_of(page): page["id"] for page in self._all_pages(self.ingredients_ds)
        }
        recipes_schema = self.client.data_sources.retrieve(data_source_id=self.recipes_ds)
        ingredients_schema = self.client.data_sources.retrieve(
            data_source_id=self.ingredients_ds
        )
        return Vocabulary(
            ingredients={name: pid for name, pid in ingredients.items() if name},
            cuisines=_options(recipes_schema, "Cuisine"),
            meals=_options(recipes_schema, "Meal"),
            categories=_options(ingredients_schema, "Category"),
        )

    def find_by_url(self, url: str) -> str | None:
        target = canonical_url(url)
        if not target:
            return None
        result = self.client.data_sources.query(
            data_source_id=self.recipes_ds,
            filter={"property": "Source URL", "url": {"equals": target}},
            page_size=1,
        )
        pages = result["results"]
        return pages[0]["url"] if pages else None

    def create_ingredient(self, name: str, category: str, categories: list[str]) -> str:
        chosen = _snap_category(category, categories)
        page = self.client.pages.create(
            parent={"type": "data_source_id", "data_source_id": self.ingredients_ds},
            properties={
                "Name": {"title": [{"type": "text", "text": {"content": name[:2000]}}]},
                "Category": {"select": {"name": chosen}},
                "In pantry": {"checkbox": False},
            },
        )
        return page["id"]

    def create_recipe(
        self, recipe: Recipe, ingredient_page_ids: list[str], vocab: Vocabulary
    ) -> str:
        meals = _meal_options(recipe.meal, vocab.meals)
        properties = {
            "Name": {"title": _rt(recipe.name)},
            "Source": {"select": {"name": recipe.source}},
            "Meal": {"multi_select": [{"name": name} for name in meals]},
            "Difficulty": {"select": {"name": recipe.difficulty}},
            "Time (min)": {"number": recipe.time_min},
            "Servings": {"number": recipe.servings},
            "Ingredients": {"relation": [{"id": pid} for pid in ingredient_page_ids]},
        }
        cuisine = _cuisine_option(recipe.cuisine, vocab.cuisines)
        if cuisine:
            properties["Cuisine"] = {"select": {"name": cuisine}}
        if recipe.source_url:
            properties["Source URL"] = {"url": recipe.source_url}

        blocks = _body_blocks(recipe)
        create_args = {
            "parent": {"type": "data_source_id", "data_source_id": self.recipes_ds},
            "properties": properties,
            "children": blocks[:CHILDREN_LIMIT],
        }
        if recipe.image_url:
            create_args["cover"] = {"type": "external", "external": {"url": recipe.image_url}}

        page = self.client.pages.create(**create_args)
        try:
            for batch in batched(blocks[CHILDREN_LIMIT:], CHILDREN_LIMIT):
                self.client.blocks.children.append(block_id=page["id"], children=list(batch))
        except Exception:
            log.exception("failed to append remaining blocks to %s", page["url"])
        return page["url"]

    def save_recipe(
        self, recipe: Recipe, vocab: Vocabulary, merges: dict[str, str] | None = None
    ) -> tuple[str, bool]:
        existing = self.find_by_url(recipe.source_url)
        if existing:
            return existing, False

        merges = merges or {}
        plan = reconcile_ingredients(vocab, recipe.ingredients)
        categories = {item.name: item.category for item in recipe.ingredients}
        page_ids = list(plan.existing.values())

        undecided = list(plan.new)
        for proposed in plan.near:
            target = merges.get(proposed)
            if target and target in vocab.ingredients:
                page_ids.append(vocab.ingredients[target])
            else:
                undecided.append(proposed)

        created: dict[str, str] = {}
        for name in undecided:
            key = name.strip().lower()
            if key not in created:
                created[key] = self.create_ingredient(
                    name, categories.get(name, ""), vocab.categories
                )
            page_ids.append(created[key])

        url = self.create_recipe(recipe, list(dict.fromkeys(page_ids)), vocab)
        return url, True
