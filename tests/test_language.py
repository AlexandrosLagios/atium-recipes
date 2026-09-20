import asyncio
from unittest.mock import AsyncMock

import pytest

from recipebot import bot, llm, strings
from recipebot.config import Config
from recipebot.models import Ingredient, Recipe
from recipebot.notion import Vocabulary
from recipebot.strings import t
from recipebot.users import UserRecord

VOCAB = Vocabulary(ingredients={}, cuisines=[], meals=[], categories=[])


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


def a_recipe() -> Recipe:
    return Recipe(
        name="Κοτόπουλο λεμονάτο",
        cuisine="Greek",
        meal=["Dinner"],
        difficulty="Easy",
        time_min=90,
        servings=4,
        ingredients=[Ingredient(name="Κοτόπουλο")],
        method=["Ρόδισε το κοτόπουλο."],
        source="Web",
        source_url="https://example.com/lemonato",
    )


class FakeUsers:
    def __init__(self, record=None):
        self.record = record

    def get(self, telegram_user_id):
        return self.record

    def save(self, record):
        self.record = record

    def delete(self, telegram_user_id):
        self.record = None


class FakeStore:
    def vocabulary(self):
        return VOCAB

    def find_by_url(self, url):
        return None


def make_context(record=None, store=None):
    context = type("C", (), {})()
    context.bot_data = {
        "cfg": a_config(),
        "users": FakeUsers(record),
        "extractor": object(),
        "_store": store or FakeStore(),
    }
    return context


def make_update(text=None, data=None, language_code=None):
    update = type("U", (), {})()
    update.effective_user = type("User", (), {"id": 1, "language_code": language_code})()
    update.message = type("M", (), {})()
    update.message.text = text
    update.message.caption = None
    update.message.photo = []
    update.message.reply_to_message = None
    update.message.reply_text = AsyncMock()
    update.effective_message = update.message
    update.callback_query = None
    if data is not None:
        query = type("Q", (), {})()
        query.data = data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
    return update


@pytest.fixture(autouse=True)
def route_to_the_fake_store(monkeypatch):
    async def fake_call_with_reconnect(chat_id, context, fn):
        return await asyncio.to_thread(fn, context.bot_data["_store"])

    monkeypatch.setattr(bot, "call_with_reconnect", fake_call_with_reconnect)
    bot.PREVIEWS.clear()


def test_every_sentence_is_written_in_both_languages():
    missing = {key: set(strings.LANGUAGES) - set(value) for key, value in strings.TEXT.items()}

    assert {key: gap for key, gap in missing.items() if gap} == {}


def test_a_telegram_language_code_maps_onto_a_language_the_bot_speaks():
    assert strings.from_code("el") == "el"
    assert strings.from_code("el-GR") == "el"
    assert strings.from_code("en-GB") == "en"
    assert strings.from_code("de") == "en"
    assert strings.from_code("") == "en"


def test_greek_tells_the_model_to_write_greek_and_keep_the_select_values_english():
    prompt = llm.system_prompt_for("el")

    assert "Write in Greek" in prompt
    assert "Write in English" not in prompt
    assert "cuisine, meal, difficulty and ingredient category stay in English" in prompt
    assert "never take a Greek abbreviation" in prompt


def test_an_unknown_language_falls_back_to_the_english_rules():
    assert llm.system_prompt_for("de") == llm.system_prompt_for("en")
    assert "Write in English" in llm.patch_prompt_for("en")


async def test_tapping_a_language_stores_it_and_answers_in_it():
    context = make_context(a_user_record())
    update = make_update(data="lang:el")

    await bot.on_callback(update, context)

    assert context.bot_data["users"].record.language == "el"
    said = update.callback_query.edit_message_text.call_args[0][0]
    assert said == t("el", "language_set", language="Ελληνικά")


async def test_a_language_tap_before_connecting_changes_nothing():
    context = make_context(None)
    update = make_update(data="lang:el")

    await bot.on_callback(update, context)

    assert context.bot_data["users"].record is None
    assert update.callback_query.edit_message_text.call_args[0][0] == t("el", "connect")


async def test_the_language_command_offers_every_language():
    context = make_context(a_user_record(language="el"))
    update = make_update(text="/language")

    await bot.on_language_command(update, context)

    call = update.message.reply_text.call_args
    assert call[0][0] == t("el", "language_prompt")
    buttons = [b for row in call[1]["reply_markup"].inline_keyboard for b in row]
    assert [(b.text, b.callback_data) for b in buttons] == [
        ("English", "lang:en"),
        ("Ελληνικά", "lang:el"),
    ]


async def test_a_greek_user_gets_a_greek_preview_and_a_greek_extraction(monkeypatch):
    seen = []
    monkeypatch.setattr(
        bot, "from_url", lambda url, extractor, vocab, language: seen.append(language) or a_recipe()
    )
    context = make_context(a_user_record(language="el"))
    update = make_update(text="https://example.com/lemonato")

    await bot.on_text(update, context)

    assert seen == ["el"]
    call = update.message.reply_text.call_args
    assert t("el", "preview_reply") in call[0][0]
    assert "4 μερίδες" in call[0][0]
    labels = [b.text for row in call[1]["reply_markup"].inline_keyboard for b in row]
    assert labels == ["Αποθήκευση", "Απόρριψη"]


async def test_a_correction_to_a_greek_preview_is_patched_in_greek(monkeypatch):
    seen = []

    class Patcher:
        def patch(self, current, instruction, vocab, language):
            seen.append(language)
            return None

    context = make_context(a_user_record(language="el"))
    context.bot_data["extractor"] = Patcher()
    bot.PREVIEWS["tok"] = bot.Preview(
        recipe=a_recipe(), vocab=VOCAB, chat_id=1, message_id=77, language="el"
    )
    update = make_update(text="οι μερίδες είναι 2")
    update.message.reply_to_message = type("R", (), {"message_id": 77})()

    await bot.on_text(update, context)

    assert seen == ["el"]
    assert update.message.reply_text.call_args[0][0] == t("el", "no_patch")


async def test_a_new_user_reads_the_connect_prompt_in_their_telegram_language():
    context = make_context(None)
    update = make_update(text="hello", language_code="el-GR")

    await bot.on_text(update, context)

    call = update.message.reply_text.call_args
    assert call[0][0] == t("el", "connect")
    assert call[1]["reply_markup"].inline_keyboard[0][0].text == t("el", "connect_button")


async def test_a_reimport_answers_in_the_language_its_prompt_was_written_in():
    # The user ran /language between the prompt and the tap, so the record now
    # says English while the prompt on screen is still Greek.
    context = make_context(a_user_record(language="en"))
    bot.REIMPORTS["tok"] = bot.Reimport(
        page_id="page-1",
        page_url="https://notion.so/old",
        source_url="https://example.com/lemonato",
        language="el",
    )
    update = make_update(data="keep:tok")

    await bot.on_callback(update, context)

    assert update.callback_query.edit_message_text.call_args[0][0] == t(
        "el", "reimport_kept", url="https://notion.so/old"
    )
