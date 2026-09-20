import asyncio
from unittest.mock import AsyncMock

import pytest

from recipebot import bot
from recipebot.config import Config
from recipebot.models import Ingredient, Recipe
from recipebot.notion import IngredientPlan, Vocabulary, reconcile_ingredients
from recipebot.strings import t
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


# The preview a callback update acts on: user 1, message 77.
KEY = (1, 77)


def make_query(data, message_id):
    query = type("Q", (), {})()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = type("M", (), {"message_id": message_id})()
    return query


def make_update(data, message_id=77):
    update = type("U", (), {})()
    update.effective_user = type("User", (), {"id": 1})()
    update.callback_query = make_query(data, message_id)
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
    text = bot.preview_text(a_recipe(), IngredientPlan(), "en")

    assert "Braise" in text
    assert "90" in text
    assert "Chicken" in text


def test_preview_text_names_the_near_match():
    plan = IngredientPlan(near={"Soy Sauces": "Soy sauce"})

    text = bot.preview_text(a_recipe(), plan, "en")

    assert "Soy Sauces" in text
    assert "Soy sauce" in text


def test_the_merge_button_appears_only_for_a_near_match():
    without = bot.preview_markup(IngredientPlan(), "en")
    with_near = bot.preview_markup(IngredientPlan(near={"Soy Sauces": "Soy sauce"}), "en")

    labels = [b.text for row in without.inline_keyboard for b in row]
    assert not any("merge" in label.lower() for label in labels)

    labels = [b.text for row in with_near.inline_keyboard for b in row]
    assert any("merge" in label.lower() for label in labels)


async def test_save_writes_the_recipe_and_clears_the_preview():
    store = FakeStore()
    bot.PREVIEWS[KEY] = bot.Preview(recipe=a_recipe(), vocab=VOCAB, merges={})
    update = make_update("save")

    await bot.on_callback(update, make_context(store))

    assert len(store.saved) == 1
    assert store.saved[0][1] == {}
    assert KEY not in bot.PREVIEWS
    assert "https://notion.so/new" in update.callback_query.edit_message_text.call_args[0][0]


async def test_save_and_merge_passes_the_merge_map():
    store = FakeStore()
    recipe = a_recipe([Ingredient(name="Soy Sauces")])
    plan = reconcile_ingredients(VOCAB, recipe.ingredients)
    bot.PREVIEWS[KEY] = bot.Preview(recipe=recipe, vocab=VOCAB, merges=plan.near)
    update = make_update("merge")

    await bot.on_callback(update, make_context(store))

    assert store.saved[0][1] == {"Soy Sauces": "Soy sauce"}


async def test_discard_writes_nothing():
    store = FakeStore()
    bot.PREVIEWS[KEY] = bot.Preview(recipe=a_recipe(), vocab=VOCAB, merges={})
    update = make_update("drop")

    await bot.on_callback(update, make_context(store))

    assert store.saved == []
    assert KEY not in bot.PREVIEWS


async def test_a_forgotten_preview_says_so_and_writes_nothing():
    store = FakeStore()
    update = make_update("save", message_id=999)

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
    bot.PREVIEWS[KEY] = bot.Preview(recipe=a_recipe(), vocab=VOCAB, merges={})
    update = make_update("save")

    await bot.on_callback(update, make_context(store))

    assert KEY not in bot.PREVIEWS
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
    bot.PREVIEWS[KEY] = preview
    update = make_update("save")

    await bot.on_callback(update, make_context(store))

    assert bot.PREVIEWS[KEY] is preview
    call = update.callback_query.edit_message_text.call_args
    failure_reply = call[0][0]
    assert "failed" in failure_reply.lower()
    assert "save" in failure_reply.lower()
    markup = call.kwargs["reply_markup"]
    buttons = [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]
    assert ("Save", "save") in buttons

    retry = make_update("save")
    await bot.on_callback(retry, make_context(store))

    assert KEY not in bot.PREVIEWS
    assert len(store.saved) == 1
    assert "https://notion.so/new" in retry.callback_query.edit_message_text.call_args[0][0]


async def test_a_dropped_connection_shows_the_connect_button_instead_of_a_failure(monkeypatch):
    async def fake_call_with_reconnect(chat_id, context, fn):
        raise LookupError(chat_id)

    monkeypatch.setattr(bot, "call_with_reconnect", fake_call_with_reconnect)
    monkeypatch.setattr(bot.callback_server, "start_connect", lambda *a, **k: "https://notion.example/authorize")
    store = FakeStore()
    bot.PREVIEWS[KEY] = bot.Preview(recipe=a_recipe(), vocab=VOCAB, merges={})
    update = make_update("save")

    await bot.on_callback(update, make_context(store))

    call = update.callback_query.edit_message_text.call_args
    assert t("en", "connect") in call[0][0]
    buttons = [b for row in call.kwargs["reply_markup"].inline_keyboard for b in row]
    assert buttons[0].url == "https://notion.example/authorize"


async def test_a_double_tap_on_save_writes_the_recipe_only_once():
    store = FakeStore()
    bot.PREVIEWS[KEY] = bot.Preview(recipe=a_recipe(), vocab=VOCAB, merges={})
    first = make_update("save")
    second = make_update("save")

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
    assert sum(t("en", "expired") in reply for reply in replies) == 1


class FakePatcher:
    """Stands in for Extractor: returns each queued patched recipe in turn."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def patch(self, current, instruction, vocab, language="en"):
        self.calls.append((current, instruction))
        result = self.results.pop(0)
        # Extractor.patch rebuilds from the caller's own dump, so the fields a
        # Recipe adds to an ExtractedRecipe survive the call.
        return result and result.model_copy(update={"corrections": current.corrections})


def make_reply_update(text, reply_to_message_id, patcher):
    update = type("U", (), {})()
    update.effective_user = type("User", (), {"id": 1})()
    message = type("M", (), {})()
    message.text = text
    reply_to = type("R", (), {})()
    reply_to.message_id = reply_to_message_id
    reply_to.edit_text = AsyncMock()
    message.reply_to_message = reply_to
    message.reply_text = AsyncMock()
    update.message = message
    update.effective_message = message
    update.callback_query = None
    return update


def make_text_context(store, patcher):
    context = make_context(store)
    context.bot_data["extractor"] = patcher
    return context


def a_live_preview(recipe=None, key=KEY):
    recipe = recipe or a_recipe()
    plan = reconcile_ingredients(VOCAB, recipe.ingredients)
    bot.PREVIEWS[key] = bot.Preview(recipe=recipe, vocab=VOCAB, merges=dict(plan.near))
    return bot.PREVIEWS[key]


async def test_a_reply_to_a_preview_corrects_it_in_place():
    preview = a_live_preview()
    original = preview.recipe
    patcher = FakePatcher(a_recipe(servings=2))
    context = make_text_context(FakeStore(), patcher)
    update = make_reply_update("servings is 2", 77, patcher)

    await bot.on_text(update, context)

    assert patcher.calls == [(original, "servings is 2")]
    assert preview.recipe.servings == 2
    assert preview.recipe.corrections == ["servings is 2"]
    edit = update.message.reply_to_message.edit_text.call_args
    assert "2 servings" in edit[0][0]
    assert "Corrections: servings is 2" in edit[0][0]


async def test_a_second_correction_keeps_the_first():
    a_live_preview(a_recipe(corrections=["servings is 2"], servings=2))
    patcher = FakePatcher(a_recipe(servings=2, time_min=30))
    context = make_text_context(FakeStore(), patcher)

    await bot.on_text(make_reply_update("it takes 30 minutes", 77, patcher), context)

    assert bot.PREVIEWS[KEY].recipe.corrections == ["servings is 2", "it takes 30 minutes"]


async def test_a_correction_that_changes_nothing_is_not_stored():
    preview = a_live_preview()
    patcher = FakePatcher(a_recipe())
    context = make_text_context(FakeStore(), patcher)
    update = make_reply_update("make it better", 77, patcher)

    await bot.on_text(update, context)

    assert preview.recipe.corrections == []
    assert update.message.reply_to_message.edit_text.await_count == 0
    assert "changed nothing" in update.message.reply_text.call_args[0][0]


async def test_a_correction_the_model_could_not_apply_leaves_the_preview():
    preview = a_live_preview()
    patcher = FakePatcher(None)
    context = make_text_context(FakeStore(), patcher)
    update = make_reply_update("???", 77, patcher)

    await bot.on_text(update, context)

    assert bot.PREVIEWS[KEY] is preview
    assert update.message.reply_to_message.edit_text.await_count == 0
    assert "could not apply" in update.message.reply_text.call_args[0][0]


async def test_a_correction_that_renames_an_ingredient_rebuilds_the_merge_map():
    a_live_preview()
    patcher = FakePatcher(a_recipe([Ingredient(name="Soy Sauces")]))
    context = make_text_context(FakeStore(), patcher)
    update = make_reply_update("it is soy sauce, not chicken", 77, patcher)

    await bot.on_text(update, context)

    assert bot.PREVIEWS[KEY].merges == {"Soy Sauces": "Soy sauce"}
    labels = [
        b.text
        for row in update.message.reply_to_message.edit_text.call_args[1][
            "reply_markup"
        ].inline_keyboard
        for b in row
    ]
    assert "Save and merge" in labels


async def test_a_reply_to_anything_else_is_a_new_recipe(monkeypatch):
    a_live_preview()
    monkeypatch.setattr(bot, "from_text", lambda *a, **k: None)
    patcher = FakePatcher()
    context = make_text_context(FakeStore(), patcher)
    context.bot_data["_store"].vocabulary = lambda: VOCAB
    update = make_reply_update("a recipe for soup", 999, patcher)

    await bot.on_text(update, context)

    assert patcher.calls == []
    assert t("en", "no_recipe") in update.message.reply_text.call_args[0][0]


def test_preview_text_tells_the_user_they_can_reply():
    assert "Reply to this message to correct it." in bot.preview_text(
        a_recipe(), IngredientPlan(), "en"
    )


# A Telegram message id is unique per chat, never across chats, so two allowed
# users routinely hold the same one at the same time.
async def test_a_reply_never_reaches_another_users_preview(monkeypatch):
    monkeypatch.setattr(bot, "from_text", lambda *a, **k: None)
    a_live_preview(key=(2, 77))
    patcher = FakePatcher()
    context = make_text_context(FakeStore(), patcher)
    context.bot_data["_store"].vocabulary = lambda: VOCAB
    update = make_reply_update("servings is 2", 77, patcher)

    await bot.on_text(update, context)

    assert patcher.calls == []
    assert bot.PREVIEWS[(2, 77)].recipe.corrections == []


async def test_a_save_during_the_model_call_is_not_painted_over():
    preview = a_live_preview()

    class SavingPatcher(FakePatcher):
        def patch(self, current, instruction, vocab, language="en"):
            bot.PREVIEWS.pop(KEY)
            return super().patch(current, instruction, vocab, language)

    patcher = SavingPatcher(a_recipe(servings=2))
    context = make_text_context(FakeStore(), patcher)
    update = make_reply_update("servings is 2", 77, patcher)

    await bot.on_text(update, context)

    assert update.message.reply_to_message.edit_text.await_count == 0
    assert preview.recipe.servings == 4
    assert t("en", "expired") in update.message.reply_text.call_args[0][0]
