import asyncio
from unittest.mock import AsyncMock

import pytest

from recipebot import bot
from recipebot.config import Config
from recipebot.models import Ingredient, Recipe
from recipebot.notion import IngredientPlan, Vocabulary, reconcile_ingredients
from recipebot.users import UserRecord

VOCAB = Vocabulary(
    ingredients={"Chicken": "p1", "Soy sauce": "p3"}, cuisines=[], meals=[], categories=[]
)


def a_user_record(**overrides) -> UserRecord:
    defaults = dict(
        telegram_user_id=1,
        notion_access_token="tok-1",
        notion_refresh_token="refresh-1",
        recipes_ds="ds-r",
        ingredients_ds="ds-i",
        workspace_name="Kitchen",
        connected_at=1,
    )
    return UserRecord(**{**defaults, **overrides})


class FakeUsers:
    def __init__(self, record=None):
        self.record = record
        self.deleted = []

    def get(self, telegram_user_id):
        return self.record

    def save(self, record):
        self.record = record

    def delete(self, telegram_user_id):
        self.deleted.append(telegram_user_id)
        self.record = None


def a_config(**overrides) -> Config:
    fields = dict(
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
    return Config(**{**fields, **overrides})


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
        return "https://notion.so/new", True


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
    context.bot_data = {"cfg": a_config(), "users": FakeUsers(a_user_record()), "_store": store}
    return context


@pytest.fixture(autouse=True)
def route_to_the_fake_store(monkeypatch):
    async def fake_call_with_reconnect(chat_id, context, fn):
        return await asyncio.to_thread(fn, context.bot_data["_store"])

    monkeypatch.setattr(bot, "call_with_reconnect", fake_call_with_reconnect)


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


async def test_save_reports_already_saved_when_the_store_deduped_by_source_url():
    class AlreadySavedStore:
        def __init__(self):
            self.saved = []

        def save_recipe(self, recipe, vocab, merges=None):
            self.saved.append((recipe, merges))
            return "https://notion.so/existing", False

    store = AlreadySavedStore()
    bot.PREVIEWS["tok"] = bot.Preview(recipe=a_recipe(), vocab=VOCAB, merges={})
    update = make_update("save:tok")

    await bot.on_callback(update, make_context(store))

    assert "tok" not in bot.PREVIEWS
    reply = update.callback_query.edit_message_text.call_args[0][0]
    assert "Already saved: https://notion.so/existing" in reply


async def test_a_save_failure_restores_the_preview_with_a_working_save_button():
    class FlakyStore:
        def __init__(self):
            self.calls = 0
            self.saved = []

        def save_recipe(self, recipe, vocab, merges=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("Notion 502")
            self.saved.append((recipe, merges))
            return "https://notion.so/new", True

    store = FlakyStore()
    preview = bot.Preview(recipe=a_recipe(), vocab=VOCAB, merges={})
    bot.PREVIEWS["tok"] = preview
    update = make_update("save:tok")

    await bot.on_callback(update, make_context(store))

    assert bot.PREVIEWS["tok"] is preview
    call = update.callback_query.edit_message_text.call_args
    failure_reply = call[0][0]
    assert "failed" in failure_reply.lower()
    assert "save" in failure_reply.lower()
    markup = call.kwargs["reply_markup"]
    buttons = [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]
    assert ("Save", "save:tok") in buttons

    retry = make_update("save:tok")
    await bot.on_callback(retry, make_context(store))

    assert "tok" not in bot.PREVIEWS
    assert len(store.saved) == 1
    assert "https://notion.so/new" in retry.callback_query.edit_message_text.call_args[0][0]


async def test_a_double_tap_on_save_writes_the_recipe_only_once():
    store = FakeStore()
    bot.PREVIEWS["tok"] = bot.Preview(recipe=a_recipe(), vocab=VOCAB, merges={})
    first = make_update("save:tok")
    second = make_update("save:tok")

    await asyncio.gather(
        bot.on_callback(first, make_context(store)),
        bot.on_callback(second, make_context(store)),
    )

    assert len(store.saved) == 1
    replies = [
        first.callback_query.edit_message_text.call_args[0][0],
        second.callback_query.edit_message_text.call_args[0][0],
    ]
    assert sum("Saved" in reply for reply in replies) == 1
    assert sum(bot.EXPIRED_MESSAGE in reply for reply in replies) == 1
