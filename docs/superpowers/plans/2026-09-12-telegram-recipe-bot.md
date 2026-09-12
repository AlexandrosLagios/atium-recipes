# Telegram Recipe Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Telegram bot that turns a shared URL, Instagram or TikTok post, photo, or pasted text into a page in the Notion Recipes database, with its ingredients linked into the Ingredients database.

**Architecture:** One Python service on long polling, so the VPS opens no inbound port. A single allowlist gate drops every update from a foreign user before any parsing or any paid API call. Five extraction paths converge on one `Recipe` object; a high-confidence result writes to Notion at once, a low-confidence result becomes an in-memory preview that the user confirms with a button.

**Tech Stack:** Python 3.12, `python-telegram-bot` 22.x, `anthropic` 1.x (Haiku 4.5, escalating to Sonnet 5), `notion-client` 3.1.0, `recipe-scrapers`, `trafilatura`, `yt-dlp`, `ffmpeg`, `pydantic` 2.x, `pytest` + `pytest-asyncio`, Docker Compose.

**Spec:** `~/.local/state/superpowers/atium-recipes/handoffs/2026-09-12-recipe-bot-plan-handoff.md`. Task 1 copies its settled design into `docs/specs/2026-09-12-telegram-recipe-bot.md` so the plan travels with the spec inside the repository.

## Global Constraints

Every task's requirements implicitly include this section.

- **Allowlist is a trust boundary.** Drop every update whose `from.id` does not equal `TELEGRAM_ALLOWED_USER_ID`, before parsing and before any API call that costs money. Never make it optional, never add a bypass flag.
- **The GitHub repository is public.** Never commit a secret and never commit a Notion ID. Every token and every Notion database, data-source, and page ID is read from the environment. `.env` is already in `.gitignore`.
- **Notion API version is `2025-09-03`**, the default of `notion-client` 3.1.0. Do not pin a different one. Page parent is `{"type": "data_source_id", "data_source_id": "..."}`. Query with `client.data_sources.query(data_source_id=...)`, not `databases.query`.
- **Never write `Missing` or `Missing count`.** They are a UI-created formula and a rollup. Never attempt to create a Notion formula through the API; it rejects `current`.
- **Never set `Rating`.** An empty `Rating` means not tried.
- **Strip the query string and the fragment** from the source URL before the dedupe query and before the write. Keep the path exactly as given, trailing slash included.
- **Ingredient names are shopping level and singular.** "Chicken", not "boneless chicken thighs". The quantity and the form stay in the page body only.
- **A new ingredient row is created with `In pantry` unticked.**
- **`Time (min)` is total time including resting.**
- **Page body layout:** `## Ingredients` as a bulleted list with quantities, `## Method` as a numbered list, `## Notes` left empty, then a collapsed `Source text` toggle holding the caption, scraped body, or vision transcript.
- **The database holds English.** Where a Greek ingredient has no honest English equivalent, keep the transliterated term and add a gloss, for example `anthotyro (Greek whey cheese)`. Never substitute an approximate name.
- **A Notion select option name must not contain a comma.** The API rejects it.
- **Notion limits:** 2000 characters per rich-text object, 100 child blocks per request.
- **Models:** `claude-haiku-4-5` first. Escalate to `claude-sonnet-5` only when a parse returns empty. Never pass `output_config.effort` to Haiku 4.5; it returns 400. Never append a date suffix to a model ID.
- **Dependencies:** add none beyond the list in Task 1 without saying why the stdlib or an installed dependency cannot do the job.

---

### Task 1: Project skeleton, config, and the spec file

**Files:**
- Create: `pyproject.toml`
- Create: `src/recipebot/__init__.py`
- Create: `src/recipebot/config.py`
- Create: `.env.example`
- Create: `docs/specs/2026-09-12-telegram-recipe-bot.md`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `recipebot.config.Config` with fields `telegram_token: str`, `allowed_user_id: int`, `notion_token: str`, `recipes_ds: str`, `ingredients_ds: str`, `anthropic_key: str`; and `Config.from_env() -> Config`.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "recipebot"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "python-telegram-bot>=22.0",
    "anthropic>=1.0",
    "notion-client>=3.1.0",
    "recipe-scrapers>=15.0",
    "trafilatura>=2.0",
    "yt-dlp>=2025.1.1",
    "pydantic>=2.0",
    "httpx",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.24"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/recipebot"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["src"]
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_config.py
import pytest
from recipebot.config import Config


def test_from_env_reads_every_field(monkeypatch):
    for key, value in {
        "TELEGRAM_TOKEN": "tok",
        "TELEGRAM_ALLOWED_USER_ID": "12345",
        "NOTION_TOKEN": "ntn",
        "NOTION_RECIPES_DS": "ds-recipes",
        "NOTION_INGREDIENTS_DS": "ds-ingredients",
        "ANTHROPIC_API_KEY": "sk-ant",
    }.items():
        monkeypatch.setenv(key, value)

    cfg = Config.from_env()

    assert cfg.allowed_user_id == 12345
    assert cfg.recipes_ds == "ds-recipes"


def test_from_env_names_the_missing_variable(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "1")
    monkeypatch.setenv("NOTION_TOKEN", "n")
    monkeypatch.setenv("NOTION_RECIPES_DS", "r")
    monkeypatch.setenv("NOTION_INGREDIENTS_DS", "i")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")

    with pytest.raises(RuntimeError, match="TELEGRAM_TOKEN"):
        Config.from_env()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recipebot'`

- [ ] **Step 4: Write the implementation**

```python
# src/recipebot/config.py
import os
from dataclasses import dataclass

_VARS = [
    "TELEGRAM_TOKEN",
    "TELEGRAM_ALLOWED_USER_ID",
    "NOTION_TOKEN",
    "NOTION_RECIPES_DS",
    "NOTION_INGREDIENTS_DS",
    "ANTHROPIC_API_KEY",
]


@dataclass(frozen=True)
class Config:
    telegram_token: str
    allowed_user_id: int
    notion_token: str
    recipes_ds: str
    ingredients_ds: str
    anthropic_key: str

    @classmethod
    def from_env(cls) -> "Config":
        missing = [name for name in _VARS if not os.environ.get(name)]
        if missing:
            raise RuntimeError(f"missing environment variables: {', '.join(missing)}")
        return cls(
            telegram_token=os.environ["TELEGRAM_TOKEN"],
            allowed_user_id=int(os.environ["TELEGRAM_ALLOWED_USER_ID"]),
            notion_token=os.environ["NOTION_TOKEN"],
            recipes_ds=os.environ["NOTION_RECIPES_DS"],
            ingredients_ds=os.environ["NOTION_INGREDIENTS_DS"],
            anthropic_key=os.environ["ANTHROPIC_API_KEY"],
        )
```

Create `src/recipebot/__init__.py` as an empty file.

- [ ] **Step 5: Write `.env.example`**

```bash
# Copy to .env on the VPS and fill in. Never commit .env.
TELEGRAM_TOKEN=
TELEGRAM_ALLOWED_USER_ID=
NOTION_TOKEN=
NOTION_RECIPES_DS=
NOTION_INGREDIENTS_DS=
ANTHROPIC_API_KEY=
```

- [ ] **Step 6: Write the spec file**

Create `docs/specs/2026-09-12-telegram-recipe-bot.md`. Copy the "Settled design", "The Notion schema, as actually built", and "Rules the bot must obey" sections from the handoff at `~/.local/state/superpowers/atium-recipes/handoffs/2026-09-12-recipe-bot-plan-handoff.md`. Replace every literal Notion ID with the name of the environment variable that carries it (`NOTION_RECIPES_DS`, `NOTION_INGREDIENTS_DS`), because the repository is public. Keep the property names and the option lists; they are not secret.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: 2 passed

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/recipebot/__init__.py src/recipebot/config.py .env.example docs/specs tests/test_config.py
git commit -m "feat: add project skeleton, env config, and the bot spec"
```

---

### Task 2: The Recipe model and URL canonicalisation

**Files:**
- Create: `src/recipebot/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `canonical_url(url: str) -> str`
  - `class Ingredient(BaseModel)`: `name: str`, `quantity: str = ""`, `category: str = ""`
  - `class ExtractedRecipe(BaseModel)`: `name: str`, `cuisine: str`, `meal: list[str]`, `difficulty: Literal["Easy", "Hard"]`, `time_min: int`, `servings: int`, `ingredients: list[Ingredient]`, `method: list[str]`
  - `class Recipe(BaseModel)`: every `ExtractedRecipe` field plus `source_url: str = ""`, `source: str`, `image_url: str = ""`, `source_text: str = ""`, `high_confidence: bool = False`
  - `Recipe.from_extracted(extracted, *, source, source_url="", image_url="", source_text="", high_confidence=False) -> Recipe`

`ExtractedRecipe` is the schema handed to Claude, so it carries only what a model can know. `Recipe` is what the bot writes, so it adds the provenance the bot itself knows.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
from recipebot.models import ExtractedRecipe, Ingredient, Recipe, canonical_url


def test_canonical_url_strips_query_and_fragment():
    url = "https://redhousespice.com/overnight-pickled-vegetables/?utm_source=x#recipe"
    assert canonical_url(url) == "https://redhousespice.com/overnight-pickled-vegetables/"


def test_canonical_url_keeps_the_trailing_slash_as_given():
    assert canonical_url("https://example.com/a") == "https://example.com/a"
    assert canonical_url("https://example.com/a/") == "https://example.com/a/"


def test_from_extracted_carries_provenance():
    extracted = ExtractedRecipe(
        name="Overnight pickled vegetables",
        cuisine="Chinese",
        meal=["Side"],
        difficulty="Easy",
        time_min=745,
        servings=4,
        ingredients=[Ingredient(name="Cucumber", quantity="2 medium", category="Vegetables and aromatics")],
        method=["Salt the cucumber.", "Rest overnight."],
    )

    recipe = Recipe.from_extracted(
        extracted,
        source="Web",
        source_url="https://redhousespice.com/overnight-pickled-vegetables/",
        image_url="https://redhousespice.com/cover.jpg",
        source_text="raw body",
        high_confidence=True,
    )

    assert recipe.time_min == 745
    assert recipe.source == "Web"
    assert recipe.high_confidence is True
    assert recipe.ingredients[0].name == "Cucumber"
    assert recipe.ingredients[0].category == "Vegetables and aromatics"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recipebot.models'`

- [ ] **Step 3: Write the implementation**

```python
# src/recipebot/models.py
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class Ingredient(BaseModel):
    name: str
    quantity: str = ""
    category: str = ""


class ExtractedRecipe(BaseModel):
    name: str
    cuisine: str
    meal: list[str]
    difficulty: Literal["Easy", "Hard"]
    time_min: int
    servings: int
    ingredients: list[Ingredient]
    method: list[str]


class Recipe(ExtractedRecipe):
    source: str
    source_url: str = ""
    image_url: str = ""
    source_text: str = ""
    high_confidence: bool = False

    @classmethod
    def from_extracted(
        cls,
        extracted: ExtractedRecipe,
        *,
        source: str,
        source_url: str = "",
        image_url: str = "",
        source_text: str = "",
        high_confidence: bool = False,
    ) -> "Recipe":
        return cls(
            **extracted.model_dump(),
            source=source,
            source_url=source_url,
            image_url=image_url,
            source_text=source_text,
            high_confidence=high_confidence,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_models.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/recipebot/models.py tests/test_models.py
git commit -m "feat: add the Recipe model and URL canonicalisation"
```

---

### Task 3: The Notion vocabulary reader

Every extraction prompt needs the existing ingredient names and the existing `Cuisine`, `Meal`, and `Category` options, so the model reuses a name instead of minting a near-duplicate. This task reads them.

**Files:**
- Create: `src/recipebot/notion.py`
- Test: `tests/test_notion_vocabulary.py`

**Interfaces:**
- Consumes: `recipebot.config.Config` from Task 1.
- Produces:
  - `class Vocabulary(BaseModel)`: `ingredients: dict[str, str]` mapping ingredient name to its Notion page ID, `cuisines: list[str]`, `meals: list[str]`, `categories: list[str]`
  - `class NotionStore` with `__init__(self, client, recipes_ds: str, ingredients_ds: str)` and `vocabulary(self) -> Vocabulary`
  - `NotionStore.from_config(cfg: Config) -> NotionStore`

`NotionStore` takes an already-built client so tests can pass a fake. `from_config` builds the real one.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_notion_vocabulary.py
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_notion_vocabulary.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recipebot.notion'`

- [ ] **Step 3: Write the implementation**

```python
# src/recipebot/notion.py
from notion_client import Client
from pydantic import BaseModel

from .config import Config


class Vocabulary(BaseModel):
    ingredients: dict[str, str] = {}
    cuisines: list[str] = []
    meals: list[str] = []
    categories: list[str] = []


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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_notion_vocabulary.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add src/recipebot/notion.py tests/test_notion_vocabulary.py
git commit -m "feat: read the Notion ingredient and select-option vocabulary"
```

---

### Task 4: The dedupe query

**Files:**
- Modify: `src/recipebot/notion.py` (add `find_by_url` to `NotionStore`)
- Test: `tests/test_notion_dedupe.py`

**Interfaces:**
- Consumes: `NotionStore` from Task 3, `canonical_url` from Task 2.
- Produces: `NotionStore.find_by_url(self, url: str) -> str | None`, returning the existing Notion page URL when the canonical source URL is already stored, and `None` otherwise.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_notion_dedupe.py
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
```

An empty URL must never run the query. A photo and a pasted text carry no source URL, and an `url.equals ""` filter would match every one of them.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_notion_dedupe.py -v`
Expected: FAIL with `AttributeError: 'NotionStore' object has no attribute 'find_by_url'`

- [ ] **Step 3: Write the implementation**

Add `canonical_url` to the `from .models import ...` line at the top of `src/recipebot/notion.py`. Tasks 5 and 6 add more names to that same line; keep one import statement per module rather than three. Then add this method to `NotionStore`:

```python
    def find_by_url(self, url: str) -> str | None:
        target = canonical_url(url)
        if not target:
            return None
        pages = self._all_pages(
            self.recipes_ds,
            filter={"property": "Source URL", "url": {"equals": target}},
        )
        return pages[0]["url"] if pages else None
```

`_all_pages` already forwards `filter` through `**kwargs`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_notion_dedupe.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/recipebot/notion.py tests/test_notion_dedupe.py
git commit -m "feat: query Source URL before writing a recipe"
```

---

### Task 5: Ingredient reconciliation

The model proposes ingredient names. This task decides which of them are the ingredient rows that already exist, which are new, and which are close enough to an existing row that the user must be asked.

**Files:**
- Modify: `src/recipebot/notion.py`
- Test: `tests/test_notion_ingredients.py`

**Interfaces:**
- Consumes: `Vocabulary` from Task 3, `Ingredient` from Task 2.
- Produces:
  - `class IngredientPlan(BaseModel)`: `existing: dict[str, str]` mapping proposed name to the Notion page ID it matched, `new: list[str]` names with no match, `near: dict[str, str]` mapping a proposed name to the existing name it resembles
  - `reconcile_ingredients(vocab: Vocabulary, ingredients: list[Ingredient]) -> IngredientPlan` (a module-level function, no Notion call)
  - `NotionStore.create_ingredient(self, name: str, category: str, categories: list[str]) -> str` returning the new page ID

A name that matches an existing row case-insensitively is `existing`. A name that `difflib` rates close to an existing row is `near`, which the bot folds into the preview message as Save-and-merge. Everything else is `new`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_notion_ingredients.py
from recipebot.models import Ingredient
from recipebot.notion import NotionStore, Vocabulary, reconcile_ingredients

VOCAB = Vocabulary(
    ingredients={"Chicken": "p1", "Cucumber": "p2", "Soy sauce": "p3"},
    cuisines=["Chinese"],
    meals=["Side"],
    categories=["Staples", "Vegetables and aromatics", "Sauces and condiments"],
)


def test_exact_match_is_case_insensitive():
    plan = reconcile_ingredients(VOCAB, [Ingredient(name="chicken")])

    assert plan.existing == {"chicken": "p1"}
    assert plan.new == []
    assert plan.near == {}


def test_a_close_name_is_reported_as_near_not_created():
    plan = reconcile_ingredients(VOCAB, [Ingredient(name="Soy Sauces")])

    assert plan.existing == {}
    assert plan.new == []
    assert plan.near == {"Soy Sauces": "Soy sauce"}


def test_a_genuinely_new_name_is_new():
    plan = reconcile_ingredients(VOCAB, [Ingredient(name="Anthotyro (Greek whey cheese)")])

    assert plan.new == ["Anthotyro (Greek whey cheese)"]
    assert plan.near == {}


class FakePages:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "new-page", "url": "https://notion.so/new-page"}


class FakeClient:
    def __init__(self):
        self.pages = FakePages()


def test_create_ingredient_leaves_in_pantry_unticked_and_snaps_the_category():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    page_id = store.create_ingredient("Anthotyro", "Dairy, and eggs", VOCAB.categories)

    assert page_id == "new-page"
    props = client.pages.created[0]["properties"]
    assert props["In pantry"]["checkbox"] is False
    assert props["Category"]["select"]["name"] == "Staples"
    assert client.pages.created[0]["parent"] == {
        "type": "data_source_id",
        "data_source_id": "ds-ingredients",
    }
```

`"Dairy, and eggs"` is not in `VOCAB.categories` and carries a comma, which the Notion API rejects, so it snaps to the default `Staples`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_notion_ingredients.py -v`
Expected: FAIL with `ImportError: cannot import name 'reconcile_ingredients'`

- [ ] **Step 3: Write the implementation**

Add `from difflib import get_close_matches` and `from .models import Ingredient` to the imports of `src/recipebot/notion.py`, then add:

```python
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
```

Then add this method to `NotionStore`:

```python
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
```

And this module-level helper:

```python
def _snap_category(category: str, categories: list[str]) -> str:
    candidate = category.strip()
    for known in categories:
        if candidate.lower() == known.lower():
            return known
    close = get_close_matches(candidate.lower(), [c.lower() for c in categories], n=1, cutoff=0.7)
    if close:
        return next(c for c in categories if c.lower() == close[0])
    return "Staples"
```

The category always snaps to an option that already exists, so the bot never mints a seventh category and never sends a name containing a comma.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_notion_ingredients.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/recipebot/notion.py tests/test_notion_ingredients.py
git commit -m "feat: reconcile proposed ingredients against the existing rows"
```

---

### Task 6: The recipe page writer

**Files:**
- Modify: `src/recipebot/notion.py`
- Test: `tests/test_notion_writer.py`

**Interfaces:**
- Consumes: `Recipe` from Task 2, `NotionStore` from Task 3.
- Produces: `NotionStore.create_recipe(self, recipe: Recipe, ingredient_page_ids: list[str]) -> str`, returning the new page's Notion URL.

Page properties written: `Name`, `Source URL` (canonical, omitted when empty), `Source`, `Cuisine`, `Meal`, `Difficulty`, `Time (min)`, `Servings`, `Ingredients`. Never `Rating`, never `Missing`, never `Missing count`. The cover is the source image when there is one.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_notion_writer.py
from recipebot.models import Ingredient, Recipe
from recipebot.notion import NotionStore


class FakePages:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "r1", "url": "https://notion.so/r1"}


class FakeChildren:
    def __init__(self):
        self.appended = []

    def append(self, **kwargs):
        self.appended.append(kwargs)
        return {}


class FakeBlocks:
    def __init__(self):
        self.children = FakeChildren()


class FakeClient:
    def __init__(self):
        self.pages = FakePages()
        self.blocks = FakeBlocks()


def a_recipe(**overrides) -> Recipe:
    defaults = dict(
        name="Overnight pickled vegetables",
        cuisine="Chinese",
        meal=["Side"],
        difficulty="Easy",
        time_min=745,
        servings=4,
        ingredients=[Ingredient(name="Cucumber", quantity="2 medium")],
        method=["Salt the cucumber.", "Rest overnight."],
        source="Web",
        source_url="https://redhousespice.com/x/?utm_source=a",
        image_url="https://redhousespice.com/cover.jpg",
        source_text="raw body",
    )
    return Recipe(**{**defaults, **overrides})


def test_create_recipe_writes_the_canonical_url_and_the_cover():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    url = store.create_recipe(a_recipe(), ["p2"])

    assert url == "https://notion.so/r1"
    call = client.pages.created[0]
    assert call["parent"] == {"type": "data_source_id", "data_source_id": "ds-recipes"}
    assert call["cover"] == {
        "type": "external",
        "external": {"url": "https://redhousespice.com/cover.jpg"},
    }
    props = call["properties"]
    assert props["Source URL"]["url"] == "https://redhousespice.com/x/"
    assert props["Time (min)"]["number"] == 745
    assert props["Meal"]["multi_select"] == [{"name": "Side"}]
    assert props["Ingredients"]["relation"] == [{"id": "p2"}]
    assert "Rating" not in props
    assert "Missing" not in props
    assert "Missing count" not in props


def test_create_recipe_omits_the_url_and_the_cover_when_absent():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.create_recipe(a_recipe(source="Photo", source_url="", image_url=""), [])

    call = client.pages.created[0]
    assert "Source URL" not in call["properties"]
    assert "cover" not in call


def test_body_carries_the_three_headings_and_a_collapsed_source_toggle():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.create_recipe(a_recipe(), ["p2"])

    blocks = client.pages.created[0]["children"]
    headings = [
        b["heading_2"]["rich_text"][0]["text"]["content"]
        for b in blocks
        if b["type"] == "heading_2"
    ]
    assert headings == ["Ingredients", "Method", "Notes"]

    bullets = [b for b in blocks if b["type"] == "bulleted_list_item"]
    assert bullets[0]["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "2 medium Cucumber"

    numbered = [b for b in blocks if b["type"] == "numbered_list_item"]
    assert len(numbered) == 2

    toggle = next(b for b in blocks if b["type"] == "toggle")
    assert toggle["toggle"]["rich_text"][0]["text"]["content"] == "Source text"
    assert toggle["toggle"]["children"][0]["paragraph"]["rich_text"][0]["text"]["content"] == "raw body"


def test_more_than_a_hundred_blocks_are_appended_in_chunks():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.create_recipe(a_recipe(method=[f"Step {i}." for i in range(150)]), [])

    assert len(client.pages.created[0]["children"]) == 100
    assert client.blocks.children.appended[0]["block_id"] == "r1"
    assert len(client.blocks.children.appended[0]["children"]) <= 100
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_notion_writer.py -v`
Expected: FAIL with `AttributeError: 'NotionStore' object has no attribute 'create_recipe'`

- [ ] **Step 3: Write the block builders**

Add `from .models import Recipe` to the imports of `src/recipebot/notion.py`, then add these module-level helpers:

```python
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
```

`_block("toggle", ...)` produces a collapsed toggle, because Notion renders a new toggle collapsed.

- [ ] **Step 4: Write `create_recipe`**

Add this method to `NotionStore`:

```python
    def create_recipe(self, recipe: Recipe, ingredient_page_ids: list[str]) -> str:
        properties = {
            "Name": {"title": _rt(recipe.name)},
            "Source": {"select": {"name": recipe.source}},
            "Cuisine": {"select": {"name": recipe.cuisine.split(",")[0].strip()}},
            "Meal": {"multi_select": [{"name": meal} for meal in recipe.meal]},
            "Difficulty": {"select": {"name": recipe.difficulty}},
            "Time (min)": {"number": recipe.time_min},
            "Servings": {"number": recipe.servings},
            "Ingredients": {"relation": [{"id": pid} for pid in ingredient_page_ids]},
        }
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
        for start in range(CHILDREN_LIMIT, len(blocks), CHILDREN_LIMIT):
            self.client.blocks.children.append(
                block_id=page["id"], children=blocks[start : start + CHILDREN_LIMIT]
            )
        return page["url"]
```

`recipe.cuisine.split(",")[0]` keeps a comma out of the select option name.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_notion_writer.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add src/recipebot/notion.py tests/test_notion_writer.py
git commit -m "feat: write a recipe page with body, cover, and ingredient relation"
```

---

### Task 7: The Claude extraction client

**Files:**
- Create: `src/recipebot/llm.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `ExtractedRecipe` from Task 2, `Vocabulary` from Task 3.
- Produces:
  - `HAIKU = "claude-haiku-4-5"`, `SONNET = "claude-sonnet-5"`
  - `text_block(text: str) -> dict` and `image_block(data: bytes, media_type: str) -> dict`, the two content-block builders every path uses
  - `class Extractor` with `__init__(self, client)`, `from_config(cfg) -> Extractor`, and `extract(self, blocks: list[dict], vocab: Vocabulary) -> ExtractedRecipe | None`

`extract` calls Haiku first and escalates to Sonnet only when Haiku returns an empty parse or raises an API error. It returns `None` when both models come back empty.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm.py
import anthropic
import pytest

from recipebot.llm import HAIKU, SONNET, Extractor, image_block, text_block
from recipebot.models import ExtractedRecipe, Ingredient
from recipebot.notion import Vocabulary

VOCAB = Vocabulary(
    ingredients={"Chicken": "p1"},
    cuisines=["Chinese"],
    meals=["Side", "Dinner"],
    categories=["Staples"],
)

FULL = ExtractedRecipe(
    name="Pickles",
    cuisine="Chinese",
    meal=["Side"],
    difficulty="Easy",
    time_min=745,
    servings=4,
    ingredients=[Ingredient(name="Cucumber", quantity="2")],
    method=["Salt.", "Rest."],
)

EMPTY = FULL.model_copy(update={"ingredients": [], "method": []})


class FakeMessages:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.models = []

    def parse(self, **kwargs):
        self.models.append(kwargs["model"])
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return type("R", (), {"parsed_output": outcome})()


class FakeClient:
    def __init__(self, outcomes):
        self.messages = FakeMessages(outcomes)


def test_haiku_alone_is_enough_when_the_parse_is_complete():
    client = FakeClient([FULL])

    result = Extractor(client).extract([text_block("body")], VOCAB)

    assert result == FULL
    assert client.messages.models == [HAIKU]


def test_an_empty_parse_escalates_to_sonnet():
    client = FakeClient([EMPTY, FULL])

    result = Extractor(client).extract([text_block("body")], VOCAB)

    assert result == FULL
    assert client.messages.models == [HAIKU, SONNET]


def test_an_api_error_on_haiku_escalates_to_sonnet():
    error = anthropic.APIStatusError(
        "bad request", response=type("Resp", (), {"status_code": 400, "headers": {}})(), body=None
    )
    client = FakeClient([error, FULL])

    result = Extractor(client).extract([text_block("body")], VOCAB)

    assert result == FULL
    assert client.messages.models == [HAIKU, SONNET]


def test_two_empty_parses_return_none():
    client = FakeClient([EMPTY, EMPTY])

    assert Extractor(client).extract([text_block("body")], VOCAB) is None


def test_an_api_error_on_sonnet_is_raised():
    error = anthropic.APIStatusError(
        "bad", response=type("Resp", (), {"status_code": 500, "headers": {}})(), body=None
    )
    client = FakeClient([EMPTY, error])

    with pytest.raises(anthropic.APIStatusError):
        Extractor(client).extract([text_block("body")], VOCAB)


def test_the_vocabulary_reaches_the_system_prompt():
    client = FakeClient([FULL])
    seen = {}

    original = client.messages.parse

    def capture(**kwargs):
        seen.update(kwargs)
        return original(**kwargs)

    client.messages.parse = capture
    Extractor(client).extract([text_block("body")], VOCAB)

    assert "Chicken" in seen["system"]
    assert "Dinner" in seen["system"]
    assert "Staples" in seen["system"]


def test_image_block_is_base64():
    block = image_block(b"\x89PNG", "image/png")

    assert block["type"] == "image"
    assert block["source"]["type"] == "base64"
    assert block["source"]["media_type"] == "image/png"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_llm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recipebot.llm'`

- [ ] **Step 3: Write the implementation**

```python
# src/recipebot/llm.py
import base64

import anthropic

from .config import Config
from .models import ExtractedRecipe
from .notion import Vocabulary

HAIKU = "claude-haiku-4-5"
SONNET = "claude-sonnet-5"

SYSTEM = """You extract exactly one recipe from the material the user shares.

Rules you must follow:
- Ingredient names are shopping level and singular: "Chicken", never "boneless chicken thighs", never "Chickens". Put the quantity and the form in the quantity field instead.
- Reuse an ingredient name from the known list below whenever the ingredient matches. Mint a new name only when the ingredient is genuinely new.
- Choose cuisine, meal, and ingredient category values from the known options below whenever one fits. A value must never contain a comma.
- time_min is the total time in minutes including resting, marinating, and chilling. An overnight rest is at least 480 minutes.
- Write in English. Where a Greek or other non-English ingredient has no honest English equivalent, keep the transliterated term and add a gloss, for example "Anthotyro (Greek whey cheese)". Never substitute an approximate name.
- difficulty is "Easy" unless the recipe needs a technique a home cook would have to practise, in which case it is "Hard".
- Method steps are whole sentences in order.

If the material does not contain a recipe, return empty ingredients and an empty method."""


def _system_prompt(vocab: Vocabulary) -> str:
    return "\n\n".join(
        [
            SYSTEM,
            "Known ingredient names:\n" + ", ".join(sorted(vocab.ingredients)),
            "Known cuisines: " + ", ".join(vocab.cuisines),
            "Known meals: " + ", ".join(vocab.meals),
            "Known ingredient categories: " + ", ".join(vocab.categories),
        ]
    )


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def image_block(data: bytes, media_type: str) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(data).decode("utf-8"),
        },
    }


class Extractor:
    def __init__(self, client):
        self.client = client

    @classmethod
    def from_config(cls, cfg: Config) -> "Extractor":
        return cls(anthropic.Anthropic(api_key=cfg.anthropic_key))

    def extract(self, blocks: list[dict], vocab: Vocabulary) -> ExtractedRecipe | None:
        system = _system_prompt(vocab)
        models = (HAIKU, SONNET)
        for index, model in enumerate(models):
            last = index == len(models) - 1
            try:
                # Haiku 4.5 rejects output_config.effort with a 400, so never pass it.
                response = self.client.messages.parse(
                    model=model,
                    max_tokens=8000,
                    system=system,
                    messages=[{"role": "user", "content": blocks}],
                    output_format=ExtractedRecipe,
                )
            except anthropic.APIStatusError:
                if last:
                    raise
                continue
            parsed = response.parsed_output
            if parsed and parsed.ingredients and parsed.method:
                return parsed
        return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_llm.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/recipebot/llm.py tests/test_llm.py
git commit -m "feat: extract a recipe with Haiku, escalating to Sonnet on an empty parse"
```

---

### Task 8: The web scraping path

**Deviation from the spec, deliberate.** The spec routes a URL with JSON-LD to `recipe-scrapers` with no model at all. A JSON-LD `recipeIngredient` entry is a shopping line such as `2 medium cucumbers, thinly sliced`, and the Ingredients database needs the shopping-level singular `Cucumber`. No honest parser turns one into the other. So the scraped page still goes through one Haiku call, and the fields the scraper knows for certain (name, total time, servings, method, image) overwrite whatever the model returned. Confidence stays high, because the recipe content came from structured data and the model only normalised it.

**Files:**
- Create: `src/recipebot/scrape.py`
- Create: `tests/fixtures/jsonld_recipe.html`
- Create: `tests/fixtures/plain_article.html`
- Test: `tests/test_scrape.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `class ScrapeResult(BaseModel)`: `name: str`, `time_min: int = 0`, `servings: int = 0`, `ingredients: list[str]`, `method: list[str]`, `image_url: str = ""`, `cuisine: str = ""`
  - `ScrapeResult.as_prompt(self) -> str`
  - `fetch_html(url: str) -> str`
  - `scrape_jsonld(html: str, url: str) -> ScrapeResult | None`
  - `readable_text(html: str, url: str) -> str`

- [ ] **Step 1: Write the fixtures**

```html
<!-- tests/fixtures/jsonld_recipe.html -->
<html><head><title>Overnight pickled vegetables</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Recipe",
 "name":"Overnight pickled vegetables",
 "image":["https://example.com/cover.jpg"],
 "recipeYield":"4 servings",
 "totalTime":"PT745M",
 "recipeCuisine":"Chinese",
 "recipeIngredient":["2 medium cucumbers, thinly sliced","1 tbsp light soy sauce"],
 "recipeInstructions":[{"@type":"HowToStep","text":"Salt the cucumber."},
                       {"@type":"HowToStep","text":"Rest overnight."}]}
</script></head><body><p>Body copy.</p></body></html>
```

```html
<!-- tests/fixtures/plain_article.html -->
<html><head><title>Grandma's stew</title></head>
<body><article><h1>Grandma's stew</h1>
<p>You will need chicken, onion and a long afternoon. Brown the chicken, add the onion, simmer for two hours.</p>
</article></body></html>
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_scrape.py
from pathlib import Path

from recipebot.scrape import ScrapeResult, readable_text, scrape_jsonld

FIXTURES = Path(__file__).parent / "fixtures"
JSONLD = (FIXTURES / "jsonld_recipe.html").read_text()
PLAIN = (FIXTURES / "plain_article.html").read_text()
URL = "https://redhousespice.com/overnight-pickled-vegetables/"


def test_scrape_jsonld_reads_the_structured_fields():
    result = scrape_jsonld(JSONLD, URL)

    assert result.name == "Overnight pickled vegetables"
    assert result.time_min == 745
    assert result.servings == 4
    assert result.image_url == "https://example.com/cover.jpg"
    assert len(result.ingredients) == 2
    assert result.method == ["Salt the cucumber.", "Rest overnight."]


def test_scrape_jsonld_returns_none_without_structured_data():
    assert scrape_jsonld(PLAIN, "https://example.com/stew") is None


def test_as_prompt_carries_every_field():
    prompt = ScrapeResult(
        name="Pickles",
        time_min=745,
        servings=4,
        ingredients=["2 medium cucumbers"],
        method=["Salt."],
        cuisine="Chinese",
    ).as_prompt()

    assert "Pickles" in prompt
    assert "2 medium cucumbers" in prompt
    assert "745" in prompt


def test_readable_text_returns_the_article_body():
    text = readable_text(PLAIN, "https://example.com/stew")

    assert "chicken" in text.lower()
    assert "simmer" in text.lower()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_scrape.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recipebot.scrape'`

- [ ] **Step 4: Write the implementation**

```python
# src/recipebot/scrape.py
import logging
import re

import httpx
import trafilatura
from pydantic import BaseModel
from recipe_scrapers import scrape_html

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class ScrapeResult(BaseModel):
    name: str
    time_min: int = 0
    servings: int = 0
    ingredients: list[str] = []
    method: list[str] = []
    image_url: str = ""
    cuisine: str = ""

    def as_prompt(self) -> str:
        return "\n".join(
            [
                f"Title: {self.name}",
                f"Total time in minutes: {self.time_min}",
                f"Servings: {self.servings}",
                f"Cuisine hint: {self.cuisine}",
                "Ingredient lines:",
                *(f"- {line}" for line in self.ingredients),
                "Method:",
                *(f"{n}. {step}" for n, step in enumerate(self.method, 1)),
            ]
        )


def fetch_html(url: str) -> str:
    response = httpx.get(
        url, follow_redirects=True, timeout=20.0, headers={"User-Agent": USER_AGENT}
    )
    response.raise_for_status()
    return response.text


def _first_int(value) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else 0


# ponytail: any scraper failure means the same thing to us, "no usable structured
# data", and the caller already has a full fallback path. recipe-scrapers raises
# several unrelated exception types across versions, so catch broadly and log.
def scrape_jsonld(html: str, url: str) -> ScrapeResult | None:
    try:
        scraper = scrape_html(html, url)
        ingredients = scraper.ingredients()
        method = scraper.instructions_list()
    except Exception as exc:
        log.info("no structured recipe data for %s: %s", url, exc)
        return None
    if not ingredients or not method:
        return None

    def optional(name: str, default=""):
        try:
            return getattr(scraper, name)() or default
        except Exception:
            return default

    return ScrapeResult(
        name=optional("title") or url,
        time_min=_first_int(optional("total_time", 0)),
        servings=_first_int(optional("yields")),
        ingredients=ingredients,
        method=method,
        image_url=optional("image"),
        cuisine=optional("cuisine"),
    )


def readable_text(html: str, url: str) -> str:
    return (
        trafilatura.extract(
            html, url=url, favor_precision=True, include_comments=False
        )
        or ""
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_scrape.py -v`
Expected: 4 passed

If `scrape_html(html, url)` raises a `TypeError` about arguments, the installed version names the second parameter `org_url`. Change the call to `scrape_html(html, org_url=url)` and re-run. Do not change anything else.

- [ ] **Step 6: Commit**

```bash
git add src/recipebot/scrape.py tests/test_scrape.py tests/fixtures
git commit -m "feat: scrape structured recipe data with a readability fallback"
```

---

### Task 9: The Instagram and TikTok path

**Files:**
- Create: `src/recipebot/social.py`
- Test: `tests/test_social.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `class SocialBlocked(Exception)`
  - `class SocialResult(BaseModel)`: `caption: str`, `thumbnail_url: str = ""`, `frames: list[bytes] = []`
  - `fetch_social(url: str, workdir: Path, *, max_frames: int = 4) -> SocialResult`
  - `keyframes(video: Path, workdir: Path, max_frames: int) -> list[bytes]`

`fetch_social` raises `SocialBlocked` when `yt-dlp` cannot reach the post. The bot turns that into the screenshot prompt, which is the agreed mitigation for a datacenter IP that Instagram blocks.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_social.py
from pathlib import Path

import pytest
import yt_dlp

from recipebot import social
from recipebot.social import SocialBlocked, fetch_social


class FakeYDL:
    def __init__(self, info, workdir, error=None):
        self.info = info
        self.workdir = workdir
        self.error = error

    def __call__(self, opts):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        if self.error:
            raise self.error
        (self.workdir / "video.mp4").write_bytes(b"fake video")
        return self.info


def test_fetch_social_returns_the_caption_and_the_frames(tmp_path, monkeypatch):
    info = {"description": "Best noodles ever. 200g noodles.", "thumbnail": "https://cdn/t.jpg"}
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL(info, tmp_path))
    monkeypatch.setattr(social, "keyframes", lambda video, workdir, n: [b"frame1", b"frame2"])

    result = fetch_social("https://instagram.com/p/abc/", tmp_path)

    assert result.caption == "Best noodles ever. 200g noodles."
    assert result.thumbnail_url == "https://cdn/t.jpg"
    assert result.frames == [b"frame1", b"frame2"]


def test_fetch_social_falls_back_to_the_title_when_there_is_no_description(tmp_path, monkeypatch):
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL({"title": "Noodles"}, tmp_path))
    monkeypatch.setattr(social, "keyframes", lambda video, workdir, n: [])

    assert fetch_social("https://tiktok.com/@a/video/1", tmp_path).caption == "Noodles"


def test_a_download_error_becomes_social_blocked(tmp_path, monkeypatch):
    error = yt_dlp.utils.DownloadError("login required")
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL({}, tmp_path, error=error))

    with pytest.raises(SocialBlocked):
        fetch_social("https://instagram.com/p/abc/", tmp_path)


def test_keyframes_returns_the_bytes_ffmpeg_wrote(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")

    def fake_run(cmd, **kwargs):
        (tmp_path / "frame01.jpg").write_bytes(b"jpeg-one")
        (tmp_path / "frame02.jpg").write_bytes(b"jpeg-two")
        return None

    monkeypatch.setattr(social.subprocess, "run", fake_run)

    assert social.keyframes(video, tmp_path, 4) == [b"jpeg-one", b"jpeg-two"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_social.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recipebot.social'`

- [ ] **Step 3: Write the implementation**

```python
# src/recipebot/social.py
import logging
import subprocess
from pathlib import Path

import yt_dlp
from pydantic import BaseModel

log = logging.getLogger(__name__)

VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


class SocialBlocked(Exception):
    """yt-dlp could not reach the post. Ask the user for a screenshot instead."""


class SocialResult(BaseModel):
    caption: str = ""
    thumbnail_url: str = ""
    frames: list[bytes] = []


def keyframes(video: Path, workdir: Path, max_frames: int) -> list[bytes]:
    pattern = workdir / "frame%02d.jpg"
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y",
            "-i", str(video),
            "-vf", "fps=1/5,scale=768:-1",
            "-frames:v", str(max_frames),
            str(pattern),
        ],
        check=True,
        timeout=120,
    )
    return [path.read_bytes() for path in sorted(workdir.glob("frame*.jpg"))]


def fetch_social(url: str, workdir: Path, *, max_frames: int = 4) -> SocialResult:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "outtmpl": str(workdir / "post.%(ext)s"),
        "format": "mp4/bestvideo*+bestaudio/best",
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise SocialBlocked(str(exc)) from exc

    caption = (info.get("description") or info.get("title") or "").strip()
    result = SocialResult(caption=caption, thumbnail_url=info.get("thumbnail") or "")

    videos = [p for p in workdir.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES]
    if videos:
        try:
            result.frames = keyframes(videos[0], workdir, max_frames)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.warning("ffmpeg failed on %s: %s", url, exc)
        return result

    images = sorted(p for p in workdir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    result.frames = [p.read_bytes() for p in images[:max_frames]]
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_social.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/recipebot/social.py tests/test_social.py
git commit -m "feat: pull the caption and keyframes from an Instagram or TikTok post"
```

---

### Task 10: The router and the photo and text paths

**Files:**
- Create: `src/recipebot/extract.py`
- Test: `tests/test_extract.py`

**Interfaces:**
- Consumes: `Recipe`, `ExtractedRecipe` (Task 2), `Vocabulary` (Task 3), `Extractor`, `text_block`, `image_block` (Task 7), `scrape` (Task 8), `social` (Task 9).
- Produces:
  - `source_for(url: str) -> str` returning one of `Instagram`, `TikTok`, `YouTube`, `Web`
  - `from_url(url: str, extractor, vocab) -> Recipe | None`
  - `from_photo(images: list[tuple[bytes, str]], extractor, vocab, *, source: str = "Photo", source_url: str = "", caption: str = "", image_url: str = "", prompt: str = PHOTO_PROMPT) -> Recipe | None`
  - `from_text(text: str, extractor, vocab) -> Recipe | None`

`from_url` handles the social case itself, because the caller only has a URL. `from_photo` takes a list of `(bytes, media_type)` so the social path can reuse it for keyframes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extract.py
import pytest

from recipebot import extract
from recipebot.models import ExtractedRecipe, Ingredient
from recipebot.notion import Vocabulary
from recipebot.scrape import ScrapeResult
from recipebot.social import SocialBlocked, SocialResult

VOCAB = Vocabulary(ingredients={}, cuisines=[], meals=[], categories=[])

MODEL_SAID = ExtractedRecipe(
    name="Model title",
    cuisine="Chinese",
    meal=["Side"],
    difficulty="Easy",
    time_min=10,
    servings=1,
    ingredients=[Ingredient(name="Cucumber", quantity="2 medium")],
    method=["Model step."],
)


class StubExtractor:
    def __init__(self, result=MODEL_SAID):
        self.result = result
        self.calls = []

    def extract(self, blocks, vocab):
        self.calls.append(blocks)
        return self.result


def test_source_for_recognises_each_host():
    assert extract.source_for("https://www.instagram.com/p/abc/") == "Instagram"
    assert extract.source_for("https://vm.tiktok.com/x/") == "TikTok"
    assert extract.source_for("https://youtu.be/x") == "YouTube"
    assert extract.source_for("https://redhousespice.com/x/") == "Web"


def test_a_scraped_page_is_high_confidence_and_the_scraper_facts_win(monkeypatch):
    scraped = ScrapeResult(
        name="Overnight pickled vegetables",
        time_min=745,
        servings=4,
        ingredients=["2 medium cucumbers"],
        method=["Salt.", "Rest."],
        image_url="https://example.com/cover.jpg",
    )
    monkeypatch.setattr(extract, "fetch_html", lambda url: "<html></html>")
    monkeypatch.setattr(extract, "scrape_jsonld", lambda html, url: scraped)
    stub = StubExtractor()

    recipe = extract.from_url("https://redhousespice.com/x/?utm=1", stub, VOCAB)

    assert recipe.high_confidence is True
    assert recipe.name == "Overnight pickled vegetables"
    assert recipe.time_min == 745
    assert recipe.servings == 4
    assert recipe.method == ["Salt.", "Rest."]
    assert recipe.image_url == "https://example.com/cover.jpg"
    assert recipe.source_url == "https://redhousespice.com/x/"
    assert recipe.ingredients[0].name == "Cucumber"


def test_a_page_without_structured_data_is_low_confidence(monkeypatch):
    monkeypatch.setattr(extract, "fetch_html", lambda url: "<html></html>")
    monkeypatch.setattr(extract, "scrape_jsonld", lambda html, url: None)
    monkeypatch.setattr(extract, "readable_text", lambda html, url: "chicken and onion")

    recipe = extract.from_url("https://example.com/stew", StubExtractor(), VOCAB)

    assert recipe.high_confidence is False
    assert recipe.name == "Model title"
    assert recipe.source_text == "chicken and onion"


def test_a_social_url_sends_the_caption_and_the_frames(monkeypatch):
    result = SocialResult(caption="Best noodles", thumbnail_url="https://cdn/t.jpg", frames=[b"f1"])
    monkeypatch.setattr(extract, "fetch_social", lambda url, workdir: result)
    stub = StubExtractor()

    recipe = extract.from_url("https://www.instagram.com/p/abc/", stub, VOCAB)

    assert recipe.source == "Instagram"
    assert recipe.high_confidence is False
    assert recipe.image_url == "https://cdn/t.jpg"
    assert recipe.source_text == "Best noodles"
    kinds = [block["type"] for block in stub.calls[0]]
    assert kinds.count("image") == 1
    assert "text" in kinds


def test_a_blocked_social_url_propagates(monkeypatch):
    def blow_up(url, workdir):
        raise SocialBlocked("login required")

    monkeypatch.setattr(extract, "fetch_social", blow_up)

    with pytest.raises(SocialBlocked):
        extract.from_url("https://www.instagram.com/p/abc/", StubExtractor(), VOCAB)


def test_from_photo_sends_an_image_block_and_no_source_url():
    stub = StubExtractor()

    recipe = extract.from_photo([(b"\x89PNG", "image/png")], stub, VOCAB)

    assert recipe.source == "Photo"
    assert recipe.source_url == ""
    assert recipe.high_confidence is False
    assert stub.calls[0][0]["type"] == "image"


def test_from_text_keeps_the_pasted_text_as_source_text():
    recipe = extract.from_text("200g noodles, boil them", StubExtractor(), VOCAB)

    assert recipe.source == "Text"
    assert recipe.source_text == "200g noodles, boil them"


def test_an_empty_extraction_returns_none():
    stub = StubExtractor(result=None)

    assert extract.from_text("hello", stub, VOCAB) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_extract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recipebot.extract'`

- [ ] **Step 3: Write the implementation**

```python
# src/recipebot/extract.py
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .llm import image_block, text_block
from .models import Recipe, canonical_url
from .notion import Vocabulary
from .scrape import fetch_html, readable_text, scrape_jsonld
from .social import fetch_social

HOSTS = {
    "instagram.com": "Instagram",
    "tiktok.com": "TikTok",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
}

PHOTO_PROMPT = (
    "This image or these images show a recipe. Read every legible word, including "
    "handwriting and on-screen captions, and extract the recipe."
)
SOCIAL_PROMPT = (
    "This is a social media post. The caption follows, and the images are frames "
    "from the video. Extract the recipe from both."
)


def source_for(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    for suffix, name in HOSTS.items():
        if host == suffix or host.endswith("." + suffix):
            return name
    return "Web"


def from_url(url: str, extractor, vocab: Vocabulary) -> Recipe | None:
    source = source_for(url)
    if source in {"Instagram", "TikTok", "YouTube"}:
        return _from_social(url, source, extractor, vocab)

    html = fetch_html(url)
    scraped = scrape_jsonld(html, url)
    if scraped is None:
        body = readable_text(html, url)
        extracted = extractor.extract([text_block(body)], vocab)
        if extracted is None:
            return None
        return Recipe.from_extracted(
            extracted, source="Web", source_url=canonical_url(url), source_text=body
        )

    extracted = extractor.extract([text_block(scraped.as_prompt())], vocab)
    if extracted is None:
        return None
    recipe = Recipe.from_extracted(
        extracted,
        source="Web",
        source_url=canonical_url(url),
        image_url=scraped.image_url,
        source_text=scraped.as_prompt(),
        high_confidence=True,
    )
    # The scraper read these from structured data, so they beat the model.
    recipe.name = scraped.name or recipe.name
    recipe.time_min = scraped.time_min or recipe.time_min
    recipe.servings = scraped.servings or recipe.servings
    recipe.method = scraped.method or recipe.method
    return recipe


def _from_social(url: str, source: str, extractor, vocab: Vocabulary) -> Recipe | None:
    with tempfile.TemporaryDirectory() as tmp:
        result = fetch_social(url, Path(tmp))
        images = [(frame, "image/jpeg") for frame in result.frames]
        return from_photo(
            images,
            extractor,
            vocab,
            source=source,
            source_url=url,
            caption=result.caption,
            image_url=result.thumbnail_url,
            prompt=SOCIAL_PROMPT,
        )


def from_photo(
    images: list[tuple[bytes, str]],
    extractor,
    vocab: Vocabulary,
    *,
    source: str = "Photo",
    source_url: str = "",
    caption: str = "",
    image_url: str = "",
    prompt: str = PHOTO_PROMPT,
) -> Recipe | None:
    blocks = [image_block(data, media_type) for data, media_type in images]
    blocks.append(text_block(f"{prompt}\n\n{caption}".strip()))
    extracted = extractor.extract(blocks, vocab)
    if extracted is None:
        return None
    return Recipe.from_extracted(
        extracted,
        source=source,
        source_url=canonical_url(source_url),
        image_url=image_url,
        source_text=caption,
    )


def from_text(text: str, extractor, vocab: Vocabulary) -> Recipe | None:
    extracted = extractor.extract([text_block(text)], vocab)
    if extracted is None:
        return None
    return Recipe.from_extracted(extracted, source="Text", source_text=text)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_extract.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/recipebot/extract.py tests/test_extract.py
git commit -m "feat: route every input type to one Recipe object"
```

---

### Task 11: Saving a recipe end to end inside Notion

**Files:**
- Modify: `src/recipebot/notion.py`
- Test: `tests/test_notion_save.py`

**Interfaces:**
- Consumes: `reconcile_ingredients`, `create_ingredient` (Task 5), `create_recipe` (Task 6).
- Produces: `NotionStore.save_recipe(self, recipe: Recipe, vocab: Vocabulary, merges: dict[str, str] | None = None) -> str`

`merges` maps a proposed ingredient name to the existing ingredient name the user chose to merge it into. A proposed name that is absent from `merges` gets a new row. The bot never calls `save_recipe` with an undecided near-match, because a near-match forces the preview.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_notion_save.py
from recipebot.models import Ingredient, Recipe
from recipebot.notion import NotionStore, Vocabulary

VOCAB = Vocabulary(
    ingredients={"Chicken": "p1", "Soy sauce": "p3"},
    cuisines=["Chinese"],
    meals=["Dinner"],
    categories=["Staples", "Protein"],
)


class FakePages:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return {"id": f"page{len(self.created)}", "url": f"https://notion.so/page{len(self.created)}"}


class FakeChildren:
    def append(self, **kwargs):
        return {}


class FakeBlocks:
    def __init__(self):
        self.children = FakeChildren()


class FakeClient:
    def __init__(self):
        self.pages = FakePages()
        self.blocks = FakeBlocks()


def a_recipe(ingredients) -> Recipe:
    return Recipe(
        name="Braise",
        cuisine="Chinese",
        meal=["Dinner"],
        difficulty="Easy",
        time_min=90,
        servings=4,
        ingredients=ingredients,
        method=["Brown.", "Simmer."],
        source="Web",
        source_url="https://example.com/braise",
    )


def test_an_existing_ingredient_is_linked_not_created():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.save_recipe(a_recipe([Ingredient(name="chicken", category="Protein")]), VOCAB)

    assert len(client.pages.created) == 1
    assert client.pages.created[0]["properties"]["Ingredients"]["relation"] == [{"id": "p1"}]


def test_a_new_ingredient_is_created_first_then_linked():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.save_recipe(a_recipe([Ingredient(name="Star anise", category="Staples")]), VOCAB)

    assert len(client.pages.created) == 2
    ingredient_call, recipe_call = client.pages.created
    assert ingredient_call["properties"]["Name"]["title"][0]["text"]["content"] == "Star anise"
    assert ingredient_call["properties"]["In pantry"]["checkbox"] is False
    assert recipe_call["properties"]["Ingredients"]["relation"] == [{"id": "page1"}]


def test_a_merge_decision_links_the_existing_row():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.save_recipe(
        a_recipe([Ingredient(name="Soy Sauces", category="Staples")]),
        VOCAB,
        merges={"Soy Sauces": "Soy sauce"},
    )

    assert len(client.pages.created) == 1
    assert client.pages.created[0]["properties"]["Ingredients"]["relation"] == [{"id": "p3"}]


def test_a_repeated_ingredient_is_linked_once():
    client = FakeClient()
    store = NotionStore(client, "ds-recipes", "ds-ingredients")

    store.save_recipe(
        a_recipe([Ingredient(name="Chicken"), Ingredient(name="chicken")]), VOCAB
    )

    assert client.pages.created[0]["properties"]["Ingredients"]["relation"] == [{"id": "p1"}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_notion_save.py -v`
Expected: FAIL with `AttributeError: 'NotionStore' object has no attribute 'save_recipe'`

- [ ] **Step 3: Write the implementation**

Add this method to `NotionStore`:

```python
    def save_recipe(
        self, recipe: Recipe, vocab: Vocabulary, merges: dict[str, str] | None = None
    ) -> str:
        merges = merges or {}
        plan = reconcile_ingredients(vocab, recipe.ingredients)
        categories = {item.name: item.category for item in recipe.ingredients}
        page_ids = list(plan.existing.values())

        undecided = list(plan.new)
        for proposed, resembles in plan.near.items():
            target = merges.get(proposed)
            if target and target in vocab.ingredients:
                page_ids.append(vocab.ingredients[target])
            else:
                undecided.append(proposed)

        for name in undecided:
            page_ids.append(
                self.create_ingredient(name, categories.get(name, ""), vocab.categories)
            )

        return self.create_recipe(recipe, list(dict.fromkeys(page_ids)))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_notion_save.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/recipebot/notion.py tests/test_notion_save.py
git commit -m "feat: create missing ingredients and link them to a new recipe"
```

---

### Task 12: The bot, the allowlist gate, and the high-confidence flow

**Files:**
- Create: `src/recipebot/bot.py`
- Test: `tests/test_bot_gate.py`
- Test: `tests/test_bot_flow.py`

**Interfaces:**
- Consumes: everything from Tasks 1 to 11.
- Produces:
  - `build_application(cfg, store, extractor) -> telegram.ext.Application`
  - `make_gate(allowed_user_id: int)` returning the `TypeHandler` callback
  - `first_url(text: str) -> str`
  - async handlers `on_start`, `on_text`, `on_photo`
  - `handle_recipe(recipe, store, vocab, update)` which writes at once or hands off to the preview (the preview itself lands in Task 13; here it replies with a placeholder that Task 13 replaces)

The gate is the trust boundary. It runs in handler group `-1`, before every other handler, and raises `ApplicationHandlerStop`, which stops the update in every group.

- [ ] **Step 1: Write the failing gate test**

```python
# tests/test_bot_gate.py
import pytest
from telegram.ext import ApplicationHandlerStop

from recipebot.bot import make_gate


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeUpdate:
    def __init__(self, user_id):
        self.effective_user = FakeUser(user_id) if user_id is not None else None


async def test_the_allowed_user_passes_the_gate():
    gate = make_gate(12345)

    await gate(FakeUpdate(12345), None)


async def test_a_foreign_user_is_stopped():
    gate = make_gate(12345)

    with pytest.raises(ApplicationHandlerStop):
        await gate(FakeUpdate(999), None)


async def test_an_update_with_no_user_is_stopped():
    gate = make_gate(12345)

    with pytest.raises(ApplicationHandlerStop):
        await gate(FakeUpdate(None), None)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_bot_gate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recipebot.bot'`

- [ ] **Step 3: Write the failing flow test**

```python
# tests/test_bot_flow.py
from unittest.mock import AsyncMock

import pytest

from recipebot import bot
from recipebot.models import Ingredient, Recipe
from recipebot.notion import Vocabulary
from recipebot.social import SocialBlocked

VOCAB = Vocabulary(ingredients={"Chicken": "p1"}, cuisines=[], meals=[], categories=[])


def a_recipe(**overrides) -> Recipe:
    defaults = dict(
        name="Braise",
        cuisine="Chinese",
        meal=["Dinner"],
        difficulty="Easy",
        time_min=90,
        servings=4,
        ingredients=[Ingredient(name="Chicken")],
        method=["Brown."],
        source="Web",
        source_url="https://example.com/braise",
        high_confidence=True,
    )
    return Recipe(**{**defaults, **overrides})


class FakeStore:
    def __init__(self, existing=None):
        self.existing = existing
        self.saved = []

    def vocabulary(self):
        return VOCAB

    def find_by_url(self, url):
        return self.existing

    def save_recipe(self, recipe, vocab, merges=None):
        self.saved.append(recipe)
        return "https://notion.so/new"


def make_update(text=None, photo=None):
    update = type("U", (), {})()
    update.effective_user = type("User", (), {"id": 1})()
    update.message = type("M", (), {})()
    update.message.text = text
    update.message.caption = None
    update.message.photo = photo or []
    update.message.reply_text = AsyncMock()
    return update


def make_context(store, extractor):
    context = type("C", (), {})()
    context.bot_data = {"store": store, "extractor": extractor}
    context.bot = type("B", (), {"get_file": AsyncMock()})()
    return context


def test_first_url_finds_the_link_inside_a_shared_message():
    text = "look at this https://redhousespice.com/x/?utm=1 nice"

    assert bot.first_url(text) == "https://redhousespice.com/x/?utm=1"
    assert bot.first_url("no link here") == ""


async def test_a_known_url_replies_with_the_existing_page_and_never_extracts(monkeypatch):
    store = FakeStore(existing="https://notion.so/old")
    called = []
    monkeypatch.setattr(bot, "from_url", lambda *a, **k: called.append(1))
    update = make_update(text="https://redhousespice.com/x/")

    await bot.on_text(update, make_context(store, object()))

    assert called == []
    assert store.saved == []
    assert "https://notion.so/old" in update.message.reply_text.call_args[0][0]


async def test_a_high_confidence_recipe_writes_at_once(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(bot, "from_url", lambda *a, **k: a_recipe())
    update = make_update(text="https://redhousespice.com/x/")

    await bot.on_text(update, make_context(store, object()))

    assert len(store.saved) == 1
    assert "https://notion.so/new" in update.message.reply_text.call_args[0][0]


async def test_a_blocked_instagram_url_asks_for_a_screenshot(monkeypatch):
    def blocked(*args, **kwargs):
        raise SocialBlocked("login required")

    monkeypatch.setattr(bot, "from_url", blocked)
    store = FakeStore()
    update = make_update(text="https://www.instagram.com/p/abc/")

    await bot.on_text(update, make_context(store, object()))

    assert store.saved == []
    assert "screenshot" in update.message.reply_text.call_args[0][0].lower()


async def test_an_empty_extraction_says_so(monkeypatch):
    monkeypatch.setattr(bot, "from_url", lambda *a, **k: None)
    store = FakeStore()
    update = make_update(text="https://example.com/not-a-recipe")

    await bot.on_text(update, make_context(store, object()))

    assert store.saved == []
    assert "recipe" in update.message.reply_text.call_args[0][0].lower()
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `python -m pytest tests/test_bot_flow.py -v`
Expected: FAIL with `AttributeError: module 'recipebot.bot' has no attribute 'first_url'`

- [ ] **Step 5: Write the implementation**

```python
# src/recipebot/bot.py
import asyncio
import logging
import re

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from .config import Config
from .extract import from_photo, from_text, from_url
from .llm import Extractor
from .models import Recipe
from .notion import NotionStore, reconcile_ingredients
from .social import SocialBlocked

log = logging.getLogger(__name__)

URL_PATTERN = re.compile(r"https?://\S+")

BLOCKED_MESSAGE = (
    "I could not open that post. Send me a screenshot of it and I will read that instead."
)
NO_RECIPE_MESSAGE = "I could not find a recipe in that."


def first_url(text: str) -> str:
    match = URL_PATTERN.search(text or "")
    return match.group() if match else ""


def make_gate(allowed_user_id: int):
    async def gate(update, context) -> None:
        user = getattr(update, "effective_user", None)
        if user is None or user.id != allowed_user_id:
            log.warning("dropped update from %s", getattr(user, "id", "unknown"))
            raise ApplicationHandlerStop

    return gate


async def handle_recipe(recipe: Recipe, store, vocab, update) -> None:
    plan = reconcile_ingredients(vocab, recipe.ingredients)
    if recipe.high_confidence and not plan.near:
        url = await asyncio.to_thread(store.save_recipe, recipe, vocab)
        await update.message.reply_text(f"Saved: {url}")
        return
    await send_preview(recipe, plan, vocab, update)


async def send_preview(recipe, plan, vocab, update) -> None:
    # Replaced in Task 13 by the Save / Save-and-merge / Discard preview.
    await update.message.reply_text(f"Preview pending for {recipe.name}")


async def _deliver(recipe_or_none, store, vocab, update) -> None:
    if recipe_or_none is None:
        await update.message.reply_text(NO_RECIPE_MESSAGE)
        return
    await handle_recipe(recipe_or_none, store, vocab, update)


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Send me a recipe link, an Instagram or TikTok post, a photo, or pasted text."
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store = context.bot_data["store"]
    extractor = context.bot_data["extractor"]
    text = update.message.text or ""
    url = first_url(text)

    if url:
        existing = await asyncio.to_thread(store.find_by_url, url)
        if existing:
            await update.message.reply_text(f"Already saved: {existing}")
            return

    vocab = await asyncio.to_thread(store.vocabulary)
    try:
        if url:
            recipe = await asyncio.to_thread(from_url, url, extractor, vocab)
        else:
            recipe = await asyncio.to_thread(from_text, text, extractor, vocab)
    except SocialBlocked:
        await update.message.reply_text(BLOCKED_MESSAGE)
        return

    await _deliver(recipe, store, vocab, update)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store = context.bot_data["store"]
    extractor = context.bot_data["extractor"]

    photo = update.message.photo[-1]
    telegram_file = await context.bot.get_file(photo.file_id)
    data = bytes(await telegram_file.download_as_bytearray())

    vocab = await asyncio.to_thread(store.vocabulary)
    recipe = await asyncio.to_thread(
        from_photo,
        [(data, "image/jpeg")],
        extractor,
        vocab,
        caption=update.message.caption or "",
    )
    await _deliver(recipe, store, vocab, update)


def build_application(cfg: Config, store: NotionStore, extractor: Extractor) -> Application:
    application = Application.builder().token(cfg.telegram_token).build()
    application.bot_data["store"] = store
    application.bot_data["extractor"] = extractor

    application.add_handler(
        TypeHandler(Update, make_gate(cfg.allowed_user_id), block=True), group=-1
    )
    application.add_handler(CommandHandler("start", on_start))
    application.add_handler(MessageHandler(filters.PHOTO, on_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return application
```

Every blocking call runs through `asyncio.to_thread`, because `httpx`, `yt-dlp`, `ffmpeg`, the Notion client, and the Anthropic client are all synchronous, and blocking the event loop would stall polling.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_bot_gate.py tests/test_bot_flow.py -v`
Expected: 8 passed

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest -v`
Expected: every test passes

- [ ] **Step 8: Commit**

```bash
git add src/recipebot/bot.py tests/test_bot_gate.py tests/test_bot_flow.py
git commit -m "feat: add the allowlist gate and the high-confidence save flow"
```

---

### Task 13: The preview with Save, Save-and-merge, and Discard

**Files:**
- Modify: `src/recipebot/bot.py`
- Test: `tests/test_bot_preview.py`

**Interfaces:**
- Consumes: `handle_recipe` and `send_preview` from Task 12.
- Produces:
  - `PREVIEWS: dict[str, Preview]` module-level store
  - `class Preview`: `recipe: Recipe`, `vocab: Vocabulary`, `merges: dict[str, str]`
  - `preview_text(recipe, plan) -> str`
  - `preview_markup(token: str, plan) -> InlineKeyboardMarkup`
  - `on_callback(update, context)` registered as `CallbackQueryHandler`

Callback data is `save:<token>`, `merge:<token>`, or `drop:<token>`. A `uuid4().hex` token is 32 characters, well inside Telegram's 64-byte limit.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bot_preview.py
from unittest.mock import AsyncMock

from recipebot import bot
from recipebot.models import Ingredient, Recipe
from recipebot.notion import IngredientPlan, Vocabulary, reconcile_ingredients

VOCAB = Vocabulary(
    ingredients={"Chicken": "p1", "Soy sauce": "p3"}, cuisines=[], meals=[], categories=[]
)


def a_recipe(ingredients=None, **overrides) -> Recipe:
    defaults = dict(
        name="Braise",
        cuisine="Chinese",
        meal=["Dinner"],
        difficulty="Easy",
        time_min=90,
        servings=4,
        ingredients=ingredients or [Ingredient(name="Chicken")],
        method=["Brown.", "Simmer."],
        source="Web",
        source_url="https://example.com/braise",
    )
    return Recipe(**{**defaults, **overrides})


class FakeStore:
    def __init__(self):
        self.saved = []

    def save_recipe(self, recipe, vocab, merges=None):
        self.saved.append((recipe, merges))
        return "https://notion.so/new"


def make_query(data):
    query = type("Q", (), {})()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return query


def make_update(data):
    update = type("U", (), {})()
    update.effective_user = type("User", (), {"id": 1})()
    update.callback_query = make_query(data)
    return update


def make_context(store):
    context = type("C", (), {})()
    context.bot_data = {"store": store}
    return context


def setup_function():
    bot.PREVIEWS.clear()


def test_preview_text_shows_the_fields_the_user_must_check():
    text = bot.preview_text(a_recipe(), IngredientPlan())

    assert "Braise" in text
    assert "90" in text
    assert "Chicken" in text


def test_preview_text_names_the_near_match():
    plan = IngredientPlan(near={"Soy Sauces": "Soy sauce"})

    text = bot.preview_text(a_recipe(), plan)

    assert "Soy Sauces" in text
    assert "Soy sauce" in text


def test_the_merge_button_appears_only_for_a_near_match():
    without = bot.preview_markup("tok", IngredientPlan())
    with_near = bot.preview_markup("tok", IngredientPlan(near={"Soy Sauces": "Soy sauce"}))

    labels = [b.text for row in without.inline_keyboard for b in row]
    assert not any("merge" in label.lower() for label in labels)

    labels = [b.text for row in with_near.inline_keyboard for b in row]
    assert any("merge" in label.lower() for label in labels)


async def test_save_writes_the_recipe_and_clears_the_preview():
    store = FakeStore()
    bot.PREVIEWS["tok"] = bot.Preview(recipe=a_recipe(), vocab=VOCAB, merges={})
    update = make_update("save:tok")

    await bot.on_callback(update, make_context(store))

    assert len(store.saved) == 1
    assert store.saved[0][1] == {}
    assert "tok" not in bot.PREVIEWS
    assert "https://notion.so/new" in update.callback_query.edit_message_text.call_args[0][0]


async def test_save_and_merge_passes_the_merge_map():
    store = FakeStore()
    recipe = a_recipe([Ingredient(name="Soy Sauces")])
    plan = reconcile_ingredients(VOCAB, recipe.ingredients)
    bot.PREVIEWS["tok"] = bot.Preview(recipe=recipe, vocab=VOCAB, merges=plan.near)
    update = make_update("merge:tok")

    await bot.on_callback(update, make_context(store))

    assert store.saved[0][1] == {"Soy Sauces": "Soy sauce"}


async def test_discard_writes_nothing():
    store = FakeStore()
    bot.PREVIEWS["tok"] = bot.Preview(recipe=a_recipe(), vocab=VOCAB, merges={})
    update = make_update("drop:tok")

    await bot.on_callback(update, make_context(store))

    assert store.saved == []
    assert "tok" not in bot.PREVIEWS


async def test_a_forgotten_preview_says_so_and_writes_nothing():
    store = FakeStore()
    update = make_update("save:gone")

    await bot.on_callback(update, make_context(store))

    assert store.saved == []
    assert "again" in update.callback_query.edit_message_text.call_args[0][0].lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_bot_preview.py -v`
Expected: FAIL with `AttributeError: module 'recipebot.bot' has no attribute 'PREVIEWS'`

- [ ] **Step 3: Write the implementation**

Add to the imports of `src/recipebot/bot.py`:

```python
import uuid
from dataclasses import dataclass, field

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler

from .notion import IngredientPlan, Vocabulary
```

Then add:

```python
# ponytail: previews live in memory on purpose. A restart forgets them and the
# user re-shares the link. Persist them only if a restart ever loses real work.
PREVIEWS: dict[str, "Preview"] = {}

EXPIRED_MESSAGE = "I no longer have that preview. Share the recipe again."


@dataclass
class Preview:
    recipe: Recipe
    vocab: Vocabulary
    merges: dict[str, str] = field(default_factory=dict)


def preview_text(recipe: Recipe, plan: IngredientPlan) -> str:
    lines = [
        recipe.name,
        f"{recipe.cuisine} | {', '.join(recipe.meal)} | {recipe.difficulty}",
        f"{recipe.time_min} min | {recipe.servings} servings",
        "",
        "Ingredients: " + ", ".join(item.name for item in recipe.ingredients),
        f"Method: {len(recipe.method)} steps",
    ]
    if plan.new:
        lines.append("New ingredient rows: " + ", ".join(plan.new))
    for proposed, resembles in plan.near.items():
        lines.append(f'"{proposed}" looks like the existing "{resembles}".')
    return "\n".join(lines)


def preview_markup(token: str, plan: IngredientPlan) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton("Save", callback_data=f"save:{token}")]
    if plan.near:
        row.append(InlineKeyboardButton("Save and merge", callback_data=f"merge:{token}"))
    row.append(InlineKeyboardButton("Discard", callback_data=f"drop:{token}"))
    return InlineKeyboardMarkup([row])
```

Replace the Task 12 placeholder `send_preview` with:

```python
async def send_preview(recipe, plan, vocab, update) -> None:
    token = uuid.uuid4().hex
    PREVIEWS[token] = Preview(recipe=recipe, vocab=vocab, merges=dict(plan.near))
    await update.message.reply_text(
        preview_text(recipe, plan), reply_markup=preview_markup(token, plan)
    )
```

And add the callback handler:

```python
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action, _, token = query.data.partition(":")

    preview = PREVIEWS.pop(token, None)
    if preview is None:
        await query.edit_message_text(EXPIRED_MESSAGE)
        return

    if action == "drop":
        await query.edit_message_text("Discarded.")
        return

    merges = preview.merges if action == "merge" else {}
    store = context.bot_data["store"]
    url = await asyncio.to_thread(store.save_recipe, preview.recipe, preview.vocab, merges)
    await query.edit_message_text(f"Saved: {url}")
```

Register it in `build_application`, after the gate:

```python
    application.add_handler(CallbackQueryHandler(on_callback))
```

`CallbackQueryHandler` takes no `filters` argument, so the group `-1` gate is the only thing standing between a foreign user and this handler. That is why the gate is a `TypeHandler` on every `Update` rather than a per-handler filter.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_bot_preview.py -v`
Expected: 7 passed

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -v`
Expected: every test passes

- [ ] **Step 6: Commit**

```bash
git add src/recipebot/bot.py tests/test_bot_preview.py
git commit -m "feat: preview a low-confidence recipe with Save, merge, and Discard"
```

---

### Task 14: Entrypoint, Docker image, and Compose service

**Files:**
- Create: `src/recipebot/__main__.py`
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`
- Modify: `README.md`
- Test: `tests/test_entrypoint.py`

**Interfaces:**
- Consumes: `build_application` (Task 12), `Config` (Task 1), `NotionStore` (Task 3), `Extractor` (Task 7).
- Produces: `recipebot.__main__.main() -> None` and a `recipebot` Compose service.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_entrypoint.py
from telegram.ext import CallbackQueryHandler, TypeHandler

from recipebot.bot import build_application
from recipebot.config import Config

CFG = Config(
    telegram_token="123:abc",
    allowed_user_id=12345,
    notion_token="ntn",
    recipes_ds="ds-r",
    ingredients_ds="ds-i",
    anthropic_key="sk-ant",
)


def test_the_gate_runs_before_every_other_handler():
    application = build_application(CFG, object(), object())

    groups = sorted(application.handlers)
    assert groups[0] == -1
    assert isinstance(application.handlers[-1][0], TypeHandler)


def test_the_callback_handler_is_registered():
    application = build_application(CFG, object(), object())

    assert any(
        isinstance(handler, CallbackQueryHandler) for handler in application.handlers[0]
    )
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/test_entrypoint.py -v`
Expected: 2 passed. These two lock the wiring Tasks 12 and 13 already produced; they are regression tests, not drivers. If either fails, fix Task 12 or Task 13 before continuing.

- [ ] **Step 3: Write the failing test for the entrypoint itself**

```python
# append to tests/test_entrypoint.py
import pytest

from recipebot import __main__ as entrypoint


def test_main_refuses_to_start_without_the_environment(monkeypatch):
    for name in ("TELEGRAM_TOKEN", "TELEGRAM_ALLOWED_USER_ID", "NOTION_TOKEN",
                 "NOTION_RECIPES_DS", "NOTION_INGREDIENTS_DS", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="TELEGRAM_TOKEN"):
        entrypoint.main()
```

`main()` must raise before it builds any client, so a missing variable never turns into a confusing Notion or Telegram authentication error.

Run: `python -m pytest tests/test_entrypoint.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'recipebot.__main__'`

- [ ] **Step 4: Write the entrypoint**

```python
# src/recipebot/__main__.py
import logging

from telegram import Update

from .bot import build_application
from .config import Config
from .llm import Extractor
from .notion import NotionStore


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cfg = Config.from_env()
    application = build_application(
        cfg, NotionStore.from_config(cfg), Extractor.from_config(cfg)
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Write the Dockerfile**

```dockerfile
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

CMD ["python", "-m", "recipebot"]
```

`yt-dlp` arrives through `pip`, so only `ffmpeg` needs `apt`.

- [ ] **Step 6: Write the Compose service and `.dockerignore`**

```yaml
# docker-compose.yml
services:
  recipebot:
    build: .
    restart: unless-stopped
    env_file: .env
```

The service publishes no port. Long polling means the VPS opens nothing inbound.

```
# .dockerignore
.git
.venv
__pycache__
tests
docs
.env
.env.*
```

- [ ] **Step 7: Update `README.md`**

Replace the `Status: shaping.` line with a run section: copy `.env.example` to `.env`, fill the four secrets and the two Notion data-source IDs, then `docker compose up -d --build`. State that the Notion integration must be shared with the Kitchen page, and that the bot answers only the Telegram user ID in `TELEGRAM_ALLOWED_USER_ID`. Do not put any real ID or token in `README.md`.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python -m pytest -v`
Expected: every test passes

- [ ] **Step 9: Verify the image builds**

Run: `docker compose build`
Expected: the build succeeds and the final stage installs `recipebot`.

- [ ] **Step 10: Commit**

```bash
git add src/recipebot/__main__.py Dockerfile docker-compose.yml .dockerignore README.md tests/test_entrypoint.py
git commit -m "feat: add the service entrypoint, Docker image, and Compose service"
```

---

### Task 15: Verify against the real Notion database

**Blocked on the user.** This task needs four secrets that only the user can create: a Telegram bot token from BotFather, the user's own Telegram user ID from `@userinfobot`, a Notion internal integration token with the Kitchen page shared to it, and an Anthropic API key. Tasks 1 to 14 run to completion without them. Do not start this task until the user confirms `.env` is filled.

**Files:**
- Modify: whatever the run exposes as broken. Every fix carries its own regression test.

- [ ] **Step 1: Fill `.env` and start the bot locally**

```bash
cp .env.example .env
```

The user fills the six values. `NOTION_RECIPES_DS` and `NOTION_INGREDIENTS_DS` are the data-source IDs recorded in project memory at `notion-data-model.md`, not the database IDs.

```bash
python -m recipebot
```

- [ ] **Step 2: Confirm the allowlist holds**

Ask the user to have a second Telegram account message the bot. Expected: no reply, and one `dropped update from <id>` line in the log. If the bot replies, stop and fix the gate before anything else.

- [ ] **Step 3: Confirm the dedupe path**

Share `https://redhousespice.com/overnight-pickled-vegetables/` with the bot. The sample recipe already holds that URL.
Expected: `Already saved: <link to the existing page>`, no new page in the Recipes database, and no Anthropic call in the log.

- [ ] **Step 4: Confirm the high-confidence path**

Share a recipe URL from a site with JSON-LD that is not already stored.
Expected: a `Saved: <link>` reply. Open the page and check it against the reference sample recipe page, whose ID is in the maintainer's private project notes and is deliberately not committed: a cover image, `## Ingredients` as a bulleted list with quantities, `## Method` as a numbered list, an empty `## Notes`, and a collapsed `Source text` toggle. Check that `Rating` is empty, that `Time (min)` counts any resting time, and that each linked ingredient row is shopping level and singular.

- [ ] **Step 5: Confirm the preview path**

Paste a recipe as plain text.
Expected: a preview message with Save and Discard. Tap Discard and confirm the Recipes database gained nothing. Repeat and tap Save, then check the page.

- [ ] **Step 6: Confirm the Instagram path and its fallback**

Share one of the user's own saved Instagram posts.
Expected: either a preview, or the screenshot message. If it is the screenshot message, send a screenshot of the same post and confirm the vision path produces a preview. Record which happened in the PR description, because the spec lists the Instagram block as UNVERIFIED.

- [ ] **Step 7: Confirm the near-match question**

Share a recipe whose ingredient list contains a near-duplicate of an existing row, for example a recipe using soy sauce when the database already holds `Soy sauce`.
Expected: the preview names both and offers Save and merge. Tap it, then confirm no duplicate ingredient row was created.

- [ ] **Step 8: Deploy**

```bash
docker compose up -d --build
docker compose logs -f recipebot
```

- [ ] **Step 9: Commit any fixes**

Each fix from this task lands as its own commit with its own regression test.

---

## Self-Review

Run before handing the plan to an executor.

**Spec coverage.** Every rule in the handoff maps to a task:

| Spec rule | Task |
| --- | --- |
| Long polling, no inbound port | 14 |
| Allowlist drops foreign updates before parsing | 12 |
| URL with JSON-LD, high confidence | 10 (see the stated deviation in Task 8) |
| URL without JSON-LD, readability then Claude | 8, 10 |
| Instagram or TikTok caption and keyframes | 9, 10 |
| Photo or screenshot through vision | 10, 12 |
| Pasted text | 10, 12 |
| Haiku first, Sonnet on an empty parse | 7 |
| High confidence writes at once and replies with the link | 12 |
| Low confidence previews with Save and Discard | 13 |
| Previews live in memory | 13 |
| Ingredient rows shopping level and singular | 7 (prompt), 15 (verified) |
| Strip query and fragment from Source URL | 2, 4, 6 |
| Inject the vocabulary into every prompt | 3, 7 |
| A new ingredient row has `In pantry` unticked | 5 |
| A near-duplicate becomes a preview question | 5, 11, 13 |
| `Time (min)` includes resting | 7 (prompt), 15 (verified) |
| Source image as the page cover | 6 |
| Body layout and the collapsed `Source text` toggle | 6 |
| English with a gloss for a Greek term | 7 (prompt) |
| Dedupe query before every write | 4, 12 |
| One Python service, one Compose service, ffmpeg and yt-dlp in the image | 14 |
| No secret committed | 1, 14 |

**Deliberate omissions**, matching the spec: no Whisper or audio transcription, no queue, no Redis, no datastore of our own, no persisted previews, no web UI.

**One open deviation.** Task 8 adds a Haiku call to the JSON-LD path, which the spec described as model-free. The reason, the scope, and the guarantee that the scraper's facts overwrite the model's are stated at the top of Task 8. Raise it with the user before Task 8 if they want the spec honoured literally instead.

---

## Execution

Per the user's standing workflow rules, execute with `superpowers:subagent-driven-development`, never with `executing-plans`. Run `test-driven-development` inside each task. Tasks 1 to 14 need no secrets; Task 15 is blocked until the user fills `.env`.

Close with `/ponytail-review` and `/simplify`, then open the PR with `manage-pr`, never with `gh pr create` by hand.
