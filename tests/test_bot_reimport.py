import asyncio
from unittest.mock import AsyncMock

import pytest

from recipebot import bot
from recipebot.config import Config
from recipebot.models import Ingredient, Recipe
from recipebot.notion import Vocabulary
from recipebot.users import UserRecord

VOCAB = Vocabulary(
    ingredients={"Chicken": "p1", "Soy sauce": "p3"}, cuisines=[], meals=[], categories=[]
)
PAGE = {"id": "page-old", "url": "https://notion.so/old"}


def a_user_record() -> UserRecord:
    return UserRecord(
        telegram_user_id=1,
        notion_access_token="tok-1",
        notion_refresh_token="refresh-1",
        recipes_ds="ds-r",
        ingredients_ds="ds-i",
        workspace_name="Kitchen",
        connected_at=1,
    )


class FakeUsers:
    def get(self, telegram_user_id):
        return a_user_record()


def a_config() -> Config:
    return Config(
        telegram_token="123:abc",
        allowed_user_ids=frozenset({1}),
        notion_client_id="c",
        notion_client_secret="s",
        notion_redirect_uri="https://bot.example/oauth/callback",
        oauth_callback_port=8080,
        db_path=":memory:",
        llm_provider="gemini",
        llm_api_key="g-key",
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
        method=["Brown."],
        source="Web",
        source_url="https://example.com/braise",
        high_confidence=True,
    )
    return Recipe(**{**defaults, **overrides})


class FakeStore:
    def __init__(self, source_text="200 g chicken"):
        self.stored_text = source_text
        self.updated = []
        self.saved = []

    def vocabulary(self):
        return VOCAB

    def source_text(self, page_id):
        return self.stored_text

    def update_recipe(self, page_id, recipe, vocab, merges=None):
        self.updated.append((page_id, recipe, merges))
        return "https://notion.so/old"

    def save_recipe(self, recipe, vocab, merges=None):
        self.saved.append(recipe)
        return "https://notion.so/new", True


def make_update(data):
    update = type("U", (), {})()
    update.effective_user = type("User", (), {"id": 1})()
    query = type("Q", (), {})()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = type("M", (), {})()
    query.message.reply_text = AsyncMock()
    update.callback_query = query
    update.effective_message = query.message
    return update


def make_context(store, extractor=None):
    context = type("C", (), {})()
    context.bot_data = {
        "cfg": a_config(),
        "users": FakeUsers(),
        "extractor": extractor or object(),
        "_store": store,
    }
    return context


@pytest.fixture(autouse=True)
def route_to_the_fake_store(monkeypatch):
    async def fake_call_with_reconnect(chat_id, context, fn):
        return await asyncio.to_thread(fn, context.bot_data["_store"])

    monkeypatch.setattr(bot, "call_with_reconnect", fake_call_with_reconnect)


def setup_function():
    bot.PREVIEWS.clear()
    bot.REIMPORTS.clear()


def a_pending_reimport(token="tok", source_url="https://example.com/braise"):
    bot.REIMPORTS[token] = bot.Reimport(
        page_id=PAGE["id"], page_url=PAGE["url"], source_url=source_url
    )


async def test_refetch_reads_the_link_again_and_rewrites_the_same_page(monkeypatch):
    seen = []
    monkeypatch.setattr(bot, "from_url", lambda url, *a, **k: seen.append(url) or a_recipe())
    store = FakeStore()
    a_pending_reimport()

    await bot.on_callback(make_update("refetch:tok"), make_context(store))

    assert seen == ["https://example.com/braise"]
    assert store.saved == []
    assert [page_id for page_id, _, _ in store.updated] == ["page-old"]


async def test_reuse_saved_text_never_reads_the_link(monkeypatch):
    monkeypatch.setattr(bot, "from_url", lambda *a, **k: pytest.fail("refetched"))
    seen = {}

    def fake_from_text(text, extractor, vocab, *, source="Text", source_url=""):
        seen.update(text=text, source=source, source_url=source_url)
        return a_recipe()

    monkeypatch.setattr(bot, "from_text", fake_from_text)
    store = FakeStore(source_text="200 g chicken")
    a_pending_reimport()

    await bot.on_callback(make_update("stored:tok"), make_context(store))

    assert seen["text"] == "200 g chicken"
    # The page keeps the Source and Source URL it was first saved with.
    assert seen["source"] == "Web"
    assert seen["source_url"] == "https://example.com/braise"
    assert [page_id for page_id, _, _ in store.updated] == ["page-old"]


async def test_reuse_saved_text_says_so_when_the_page_stored_none(monkeypatch):
    store = FakeStore(source_text="   ")
    a_pending_reimport()
    update = make_update("stored:tok")

    await bot.on_callback(update, make_context(store))

    assert store.updated == []
    assert "no saved source text" in update.callback_query.message.reply_text.call_args[0][0]


async def test_keep_writes_nothing():
    store = FakeStore()
    a_pending_reimport()
    update = make_update("keep:tok")

    await bot.on_callback(update, make_context(store))

    assert store.updated == []
    assert store.saved == []
    assert "tok" not in bot.REIMPORTS
    assert PAGE["url"] in update.callback_query.edit_message_text.call_args[0][0]


async def test_a_forgotten_reimport_says_so_and_writes_nothing():
    store = FakeStore()
    update = make_update("refetch:gone")

    await bot.on_callback(update, make_context(store))

    assert store.updated == []
    assert "again" in update.callback_query.edit_message_text.call_args[0][0].lower()


async def test_the_reply_names_the_page_it_reimported(monkeypatch):
    monkeypatch.setattr(bot, "from_url", lambda *a, **k: a_recipe())
    a_pending_reimport()
    update = make_update("refetch:tok")

    await bot.on_callback(update, make_context(FakeStore()))

    assert "Reimported: https://notion.so/old" in update.effective_message.reply_text.call_args[0][0]


# A near match still asks before it merges, and Save then writes to the same page.
async def test_a_near_match_previews_first_and_saves_in_place(monkeypatch):
    recipe = a_recipe([Ingredient(name="Soy Sauces")], high_confidence=False)
    monkeypatch.setattr(bot, "from_url", lambda *a, **k: recipe)
    store = FakeStore()
    a_pending_reimport()

    await bot.on_callback(make_update("refetch:tok"), make_context(store))

    assert store.updated == []
    token, preview = next(iter(bot.PREVIEWS.items()))
    assert preview.page_id == "page-old"

    await bot.on_callback(make_update(f"merge:{token}"), make_context(store))

    assert store.saved == []
    assert store.updated == [("page-old", recipe, {"Soy Sauces": "Soy sauce"})]


async def test_a_reimport_that_finds_no_recipe_leaves_the_page_alone(monkeypatch):
    monkeypatch.setattr(bot, "from_url", lambda *a, **k: None)
    store = FakeStore()
    a_pending_reimport()
    update = make_update("refetch:tok")

    await bot.on_callback(update, make_context(store))

    assert store.updated == []
    assert bot.NO_RECIPE_MESSAGE in update.effective_message.reply_text.call_args[0][0]
