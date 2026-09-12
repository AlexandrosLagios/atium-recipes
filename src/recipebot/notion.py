from difflib import get_close_matches

from notion_client import Client
from pydantic import BaseModel

from .config import Config
from .models import Ingredient, canonical_url


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


def _title_of(page: dict) -> str:
    spans = page["properties"]["Name"]["title"]
    return "".join(span["plain_text"] for span in spans).strip()


def _options(schema: dict, prop: str) -> list[str]:
    entry = schema["properties"].get(prop)
    if not entry:
        return []
    return [option["name"] for option in entry[entry["type"]]["options"]]


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
            if not page.get("has_more"):
                return pages
            cursor = page["next_cursor"]

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
