import json
import logging
from difflib import get_close_matches
from itertools import batched
from pathlib import Path

from notion_client import Client
from notion_client.errors import APIResponseError
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


# A database that predates the property has no Corrections on its pages until
# _backfill adds it, so a missing property reads as no corrections.
def corrections_of(page: dict) -> list[str]:
    spans = page["properties"].get("Corrections", {}).get("rich_text", [])
    text = "".join(span["plain_text"] for span in spans)
    return [line.strip() for line in text.split("\n") if line.strip()]


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
SOURCE_TEXT_HEADING = "Source text"


# Notion measures rich text in UTF-16 code units, the way JavaScript does, so
# an emoji costs two and a 2000-character Python slice comes back rejected as
# 2003. Split on that measure, and never between the halves of one character.
def _utf16_chunks(text: str, limit: int = RICH_TEXT_LIMIT) -> list[str]:
    chunks, current, used = [], [], 0
    for char in text:
        cost = 2 if ord(char) > 0xFFFF else 1
        if used + cost > limit:
            chunks.append("".join(current))
            current, used = [], 0
        current.append(char)
        used += cost
    chunks.append("".join(current))
    return chunks


def _rt(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": _utf16_chunks(text)[0]}}]


def _block(kind: str, text: str, **extra) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": _rt(text), **extra}}


def _paragraphs(text: str) -> list[dict]:
    chunks = _utf16_chunks(text)
    return [_block("paragraph", chunk) for chunk in chunks[:SOURCE_TEXT_BLOCK_LIMIT]]


# Reads a block the API returned and a block built locally alike: the first
# carries plain_text, the second only the content it was built from.
def block_text(block: dict) -> str:
    kind = block["type"]
    return "".join(
        rt.get("plain_text") or rt.get("text", {}).get("content", "")
        for rt in block[kind].get("rich_text", [])
    )


def page_children(client, block_id: str) -> list[dict]:
    out, cursor = [], None
    while True:
        page = client.blocks.children.list(block_id=block_id, start_cursor=cursor)
        out += page["results"]
        cursor = page.get("next_cursor")
        if not page.get("has_more"):
            return out


def _page_face(recipe: Recipe) -> dict:
    face = {}
    if recipe.image_url:
        face["cover"] = {"type": "external", "external": {"url": recipe.image_url}}
    if recipe.emoji:
        face["icon"] = {"type": "emoji", "emoji": recipe.emoji}
    return face


def _recipe_properties(
    recipe: Recipe, ingredient_page_ids: list[str], vocab: Vocabulary
) -> dict:
    properties = {
        "Name": {"title": _rt(recipe.name)},
        "Source": {"select": {"name": recipe.source}},
        "Meal": {"multi_select": [{"name": name} for name in _meal_options(recipe.meal, vocab.meals)]},
        "Difficulty": {"select": {"name": recipe.difficulty}},
        "Time (min)": {"number": recipe.time_min},
        "Servings": {"number": recipe.servings},
        "Ingredients": {"relation": [{"id": pid} for pid in ingredient_page_ids]},
        # Always written, so clearing the list clears the property: a reimport
        # of a page whose corrections the user deleted in Notion must not put
        # them back.
        "Corrections": {
            "rich_text": _rt("\n".join(recipe.corrections)) if recipe.corrections else []
        },
    }
    cuisine = _cuisine_option(recipe.cuisine, vocab.cuisines)
    if cuisine:
        properties["Cuisine"] = {"select": {"name": cuisine}}
    if recipe.keeps_days:
        properties["Keeps (days)"] = {"number": recipe.keeps_days}
    if recipe.source_url:
        properties["Source URL"] = {"url": recipe.source_url}
    return properties


def _body_blocks(recipe: Recipe) -> list[dict]:
    blocks = [_block("heading_2", "Ingredients")]
    group = ""
    for item in recipe.ingredients:
        if item.group != group:
            group = item.group
            if group:
                blocks.append(_block("heading_3", group))
        line = f"{item.quantity} {item.name}".strip()
        blocks.append(_block("bulleted_list_item", line))
    blocks.append(_block("heading_2", "Method"))
    blocks.extend(_block("numbered_list_item", step) for step in recipe.method)
    if recipe.notes:
        blocks.append(_block("heading_2", "Notes"))
        blocks.extend(_block("bulleted_list_item", note) for note in recipe.notes)
    blocks.append(
        _block("toggle", SOURCE_TEXT_HEADING, children=_paragraphs(recipe.source_text))
    )
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
        initial_data_source={"properties": properties},
    )
    return db["data_sources"][0]["id"]


# A rollup or formula property can reference a relation that does not exist
# yet at creation time (the reciprocal relation between Recipes and
# Ingredients is only wired up after both are created), so it is excluded
# from the create payload and added later via a data_sources.update call.
def _creatable(properties: dict) -> dict:
    return {name: config for name, config in properties.items() if config.get("type") not in ("rollup", "formula")}


def _computed(properties: dict) -> dict:
    return {name: config for name, config in properties.items() if config.get("type") in ("rollup", "formula")}


def _reciprocal_relation_property(client, ingredients_ds: str, recipes_ds: str) -> str:
    schema = client.data_sources.retrieve(data_source_id=ingredients_ds)
    for name, config in schema["properties"].items():
        if config.get("type") == "relation" and config["relation"].get("data_source_id") == recipes_ds:
            return name
    raise RuntimeError("Notion did not create the reciprocal relation on Ingredients")


# Notion hands a relation-traversing formula back out on read but refuses to
# write one in: prop("Ingredients").map(current.prop("Name")) is rejected with
# "Type error with formula", though the identical expression works when the
# property is made in the UI. Rollups traverse the same relation fine, and
# every other 2.0 construct is accepted, so this is specific to a formula that
# reads a related page. Applying one property per request keeps that single
# unsettable formula from costing the user every other property and the whole
# connect. Send them as one request again if Notion ever lifts the limit.
def _apply_properties(client, data_source_id: str, properties: dict) -> list[str]:
    skipped = []
    for name, config in properties.items():
        try:
            client.data_sources.update(
                data_source_id=data_source_id, properties={name: config}
            )
        except APIResponseError as exc:
            log.warning("Notion refused the %r property, leaving it out: %s", name, exc)
            skipped.append(name)
    return skipped


def create_user_databases(client, parent_page_id: str, fixture: dict) -> tuple[str, str]:
    """Create this user's Recipes/Ingredients pair under the page they
    shared during OAuth, matching the maintainer's schema fixture. Reuses
    an existing pair instead of duplicating it, so a retry after a partial
    failure is safe."""
    ingredients_ds = _find_existing_data_source(
        client, parent_page_id, "Ingredients"
    ) or _create_data_source(
        client, parent_page_id, "Ingredients", _creatable(fixture["ingredients"]["properties"])
    )

    recipes_ds = _find_existing_data_source(client, parent_page_id, "Recipes")
    if recipes_ds is None:
        properties = _creatable(fixture["recipes"]["properties"])
        properties["Ingredients"] = {
            "type": "relation",
            "relation": {"data_source_id": ingredients_ds, "type": "dual_property", "dual_property": {}},
        }
        recipes_ds = _create_data_source(client, parent_page_id, "Recipes", properties)

    # Re-run every call, not just on first creation: a retry after a failure
    # between creating Recipes and finishing this wiring would otherwise find
    # Recipes already there and skip the rename/rollup permanently. Notion's
    # update is idempotent here, so repeating it on an already-wired pair is
    # harmless.
    reciprocal = _reciprocal_relation_property(client, ingredients_ds, recipes_ds)
    if reciprocal != "Recipes":
        client.data_sources.update(
            data_source_id=ingredients_ds, properties={reciprocal: {"name": "Recipes"}}
        )

    skipped = _apply_properties(client, ingredients_ds, _computed(fixture["ingredients"]["properties"]))
    skipped += _apply_properties(client, recipes_ds, _computed(fixture["recipes"]["properties"]))
    if skipped:
        log.warning("built the pair under %s without %s", parent_page_id, ", ".join(skipped))

    return recipes_ds, ingredients_ds


# A user's pair of databases is built to match the fixture once, at connect
# time, so every property added to the fixture afterwards would never reach an
# already-connected user. vocabulary() tops theirs up on the way past: it
# already retrieves both live schemas for the select options, so the comparison
# costs nothing and the update only fires when something is genuinely missing.
#
# Additive by name only, never a re-send: a user who added their own Cuisine
# option keeps it. Two types are excluded because they would fail on every
# message forever rather than once: the relation-traversing formula Notion
# refuses to write (see _apply_properties), and the title, which a data source
# can only hold one of, so a user who renamed theirs would buy a rejected
# request per saved recipe.
_UNBACKFILLABLE = frozenset({"formula", "title"})


def _missing_properties(schema: dict, fixture: dict) -> dict:
    return {
        name: config
        for name, config in fixture.items()
        if name not in schema["properties"] and config.get("type") not in _UNBACKFILLABLE
    }


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
        self._backfill(recipes_schema, ingredients_schema)
        return Vocabulary(
            ingredients={name: pid for name, pid in ingredients.items() if name},
            cuisines=_options(recipes_schema, "Cuisine"),
            meals=_options(recipes_schema, "Meal"),
            categories=_options(ingredients_schema, "Category"),
        )

    # ponytail: a select property that was missing entirely reports no options
    # on this one call, because the schema above predates the backfill. The next
    # message reads it back in full, so it is not worth a second retrieve.
    def _backfill(self, recipes_schema: dict, ingredients_schema: dict) -> None:
        fixture = load_schema_fixture()
        targets = (
            (self.recipes_ds, recipes_schema, fixture["recipes"]["properties"]),
            (self.ingredients_ds, ingredients_schema, fixture["ingredients"]["properties"]),
        )
        for data_source_id, schema, properties in targets:
            missing = _missing_properties(schema, properties)
            if missing:
                log.info("adding %s to %s", ", ".join(missing), data_source_id)
                _apply_properties(self.client, data_source_id, missing)

    def find_by_url(self, url: str) -> dict | None:
        target = canonical_url(url)
        if not target:
            return None
        result = self.client.data_sources.query(
            data_source_id=self.recipes_ds,
            filter={"property": "Source URL", "url": {"equals": target}},
            page_size=1,
        )
        pages = result["results"]
        return pages[0] if pages else None

    def source_text(self, page_id: str) -> str:
        for block in page_children(self.client, page_id):
            if block["type"] == "toggle" and block_text(block) == SOURCE_TEXT_HEADING:
                return "\n".join(
                    block_text(child)
                    for child in page_children(self.client, block["id"])
                    if child["type"] == "paragraph"
                )
        return ""

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
        blocks = _body_blocks(recipe)
        create_args = {
            "parent": {"type": "data_source_id", "data_source_id": self.recipes_ds},
            "properties": _recipe_properties(recipe, ingredient_page_ids, vocab),
            "children": blocks[:CHILDREN_LIMIT],
            **_page_face(recipe),
        }

        page = self.client.pages.create(**create_args)
        try:
            for batch in batched(blocks[CHILDREN_LIMIT:], CHILDREN_LIMIT):
                self.client.blocks.children.append(block_id=page["id"], children=list(batch))
        except Exception:
            log.exception("failed to append remaining blocks to %s", page["url"])
        return page["url"]

    def _ingredient_page_ids(
        self, recipe: Recipe, vocab: Vocabulary, merges: dict[str, str]
    ) -> list[str]:
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
        return list(dict.fromkeys(page_ids))

    def save_recipe(
        self, recipe: Recipe, vocab: Vocabulary, merges: dict[str, str] | None = None
    ) -> tuple[str, bool]:
        existing = self.find_by_url(recipe.source_url)
        if existing:
            return existing["url"], False

        page_ids = self._ingredient_page_ids(recipe, vocab, merges or {})
        return self.create_recipe(recipe, page_ids, vocab), True

    def update_recipe(
        self, page_id: str, recipe: Recipe, vocab: Vocabulary, merges: dict[str, str] | None = None
    ) -> str:
        """Rewrite an existing recipe page from a fresh extraction, keeping the
        page itself, so its rating, its comments and its created time survive.
        The body is replaced wholesale, so anything hand-written on the page is
        lost."""
        page_ids = self._ingredient_page_ids(recipe, vocab, merges or {})
        # An absent property means "unchanged" to an update and "unset" to a
        # create, so name the two the new extraction may have dropped. Source URL
        # stays out: it is how the page is found again, and a reimport always
        # carries the one it was found by.
        properties = {
            "Cuisine": {"select": None},
            "Keeps (days)": {"number": None},
            **_recipe_properties(recipe, page_ids, vocab),
        }
        page = self.client.pages.update(
            page_id=page_id, properties=properties, **_page_face(recipe)
        )
        stale = page_children(self.client, page_id)
        # Append before deleting: a failure between the two leaves the old body
        # and the new one on the page, which reads worse than one body but
        # loses nothing.
        for batch in batched(_body_blocks(recipe), CHILDREN_LIMIT):
            self.client.blocks.children.append(block_id=page_id, children=list(batch))
        for block in stale:
            self.client.blocks.delete(block_id=block["id"])
        return page["url"]
