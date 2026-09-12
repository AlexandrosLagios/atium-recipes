import logging
from difflib import get_close_matches

from notion_client import Client
from pydantic import BaseModel

from .config import Config
from .models import Ingredient, Recipe, canonical_url

log = logging.getLogger(__name__)


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
    lowered = {name.lower(): page_id for name, page_id in vocab.ingredients.items()}
    by_lower = {name.lower(): name for name in vocab.ingredients}
    plan = IngredientPlan()
    for item in ingredients:
        key = item.name.strip().lower()
        if not key:
            continue
        if key in lowered:
            plan.existing[item.name] = lowered[key]
            continue
        close = get_close_matches(key, list(lowered), n=1, cutoff=0.85)
        if close:
            plan.near[item.name] = by_lower[close[0]]
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


def _clean_option_name(name: str) -> str:
    return name.split(",")[0].strip()


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


class NotionStore:
    def __init__(self, client, recipes_ds: str, ingredients_ds: str):
        self.client = client
        self.recipes_ds = recipes_ds
        self.ingredients_ds = ingredients_ds

    @classmethod
    def from_config(cls, cfg: Config) -> "NotionStore":
        return cls(Client(auth=cfg.notion_token), cfg.recipes_ds, cfg.ingredients_ds)

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
        pages = self._all_pages(
            self.recipes_ds,
            filter={"property": "Source URL", "url": {"equals": target}},
        )
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

    def create_recipe(self, recipe: Recipe, ingredient_page_ids: list[str]) -> str:
        properties = {
            "Name": {"title": _rt(recipe.name)},
            "Source": {"select": {"name": recipe.source}},
            "Meal": {
                "multi_select": [
                    {"name": cleaned}
                    for meal in recipe.meal
                    if (cleaned := _clean_option_name(meal))
                ]
            },
            "Difficulty": {"select": {"name": recipe.difficulty}},
            "Time (min)": {"number": recipe.time_min},
            "Servings": {"number": recipe.servings},
            "Ingredients": {"relation": [{"id": pid} for pid in ingredient_page_ids]},
        }
        cuisine = _clean_option_name(recipe.cuisine)
        if cuisine:
            properties["Cuisine"] = {"select": {"name": cuisine}}
        target = canonical_url(recipe.source_url)
        if target:
            properties["Source URL"] = {"url": target}

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
            for start in range(CHILDREN_LIMIT, len(blocks), CHILDREN_LIMIT):
                self.client.blocks.children.append(
                    block_id=page["id"], children=blocks[start : start + CHILDREN_LIMIT]
                )
        except Exception:
            log.exception("failed to append remaining blocks to %s", page["url"])
        return page["url"]

    def save_recipe(
        self, recipe: Recipe, vocab: Vocabulary, merges: dict[str, str] | None = None
    ) -> str:
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

        return self.create_recipe(recipe, list(dict.fromkeys(page_ids)))
