import asyncio
import datetime as dt
from unittest.mock import AsyncMock

import pytest
from telegram import Chat, Document, Message, PhotoSize, Update
from telegram.ext import filters

from recipebot import bot
from recipebot.config import Config
from recipebot.models import Ingredient, Recipe
from recipebot.notion import Vocabulary
from recipebot.social import SocialBlocked
from recipebot.strings import t
from recipebot.users import UserRecord

VOCAB = Vocabulary(ingredients={"Chicken": "p1"}, cuisines=[], meals=[], categories=[])


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
    def __init__(self, record=None, allowed=()):
        self.record = record
        self.deleted = []
        self.allowed = frozenset(allowed)

    def allowed_ids(self):
        return self.allowed

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
        return "https://notion.so/new", True


def make_update(text=None, photo=None):
    update = type("U", (), {})()
    update.effective_user = type("User", (), {"id": 1})()
    update.message = type("M", (), {})()
    update.message.text = text
    update.message.caption = None
    update.message.photo = photo or []
    update.message.reply_text = AsyncMock()
    update.effective_message = update.message
    return update


def make_context(store, extractor):
    context = type("C", (), {})()
    context.bot_data = {
        "cfg": a_config(),
        "users": FakeUsers(a_user_record()),
        "extractor": extractor,
        "_store": store,
    }
    context.bot = type("B", (), {"get_file": AsyncMock()})()
    return context


@pytest.fixture(autouse=True)
def route_to_the_fake_store(monkeypatch):
    async def fake_call_with_reconnect(chat_id, context, fn):
        return await asyncio.to_thread(fn, context.bot_data["_store"])

    monkeypatch.setattr(bot, "call_with_reconnect", fake_call_with_reconnect)


def test_first_url_finds_the_link_inside_a_shared_message():
    text = "look at this https://redhousespice.com/x/?utm=1 nice"

    assert bot.first_url(text) == "https://redhousespice.com/x/?utm=1"
    assert bot.first_url("no link here") == ""


async def test_a_known_url_offers_a_reimport_and_never_extracts_on_its_own(monkeypatch):
    store = FakeStore(
        existing={"id": "page-old", "url": "https://notion.so/old", "properties": {}}
    )
    called = []
    monkeypatch.setattr(bot, "from_url", lambda *a, **k: called.append(1))
    update = make_update(text="https://redhousespice.com/x/")

    await bot.on_text(update, make_context(store, object()))

    assert called == []
    assert store.saved == []
    call = update.message.reply_text.call_args
    assert "https://notion.so/old" in call[0][0]
    labels = [b.text for row in call[1]["reply_markup"].inline_keyboard for b in row]
    assert labels == ["Refetch link", "Reuse saved text", "Keep"]


async def test_a_scraped_recipe_previews_rather_than_saving_at_once(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(bot, "from_url", lambda *a, **k: a_recipe())
    update = make_update(text="https://redhousespice.com/x/")

    await bot.on_text(update, make_context(store, object()))

    assert store.saved == []
    call = update.message.reply_text.call_args
    assert "Braise" in call[0][0]
    assert call[1]["reply_markup"] is not None


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


async def test_a_near_match_reaches_the_merge_button(monkeypatch):
    store = FakeStore()
    near_match = a_recipe(ingredients=[Ingredient(name="Chiken")])
    monkeypatch.setattr(bot, "from_url", lambda *a, **k: near_match)
    update = make_update(text="https://redhousespice.com/x/")

    await bot.on_text(update, make_context(store, object()))

    assert store.saved == []
    call = update.message.reply_text.call_args
    assert "Braise" in call[0][0]
    labels = [b.text for row in call.kwargs["reply_markup"].inline_keyboard for b in row]
    assert any("merge" in label.lower() for label in labels)


def make_photo_update(caption=""):
    update = type("U", (), {})()
    update.effective_user = type("User", (), {"id": 1})()
    update.message = type("M", (), {})()
    update.message.text = None
    update.message.caption = caption
    update.message.photo = [type("P", (), {"file_id": "small"})(), type("P", (), {"file_id": "big"})()]
    update.message.document = None
    update.message.reply_text = AsyncMock()
    update.effective_message = update.message
    return update


def make_document_update(mime_type="application/pdf", file_size=1024, caption=""):
    update = make_photo_update(caption=caption)
    update.message.photo = []
    update.message.document = type(
        "D", (), {"file_id": "doc", "mime_type": mime_type, "file_size": file_size}
    )()
    return update


def make_photo_context(store, extractor, data=b"\xff\xd8\xff"):
    telegram_file = type("F", (), {})()
    telegram_file.download_as_bytearray = AsyncMock(return_value=bytearray(data))
    context = type("C", (), {})()
    context.bot_data = {
        "cfg": a_config(),
        "users": FakeUsers(a_user_record()),
        "extractor": extractor,
        "_store": store,
    }
    context.bot = type("B", (), {"get_file": AsyncMock(return_value=telegram_file)})()
    return context


async def test_a_photo_recipe_previews_rather_than_saving_at_once(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(bot, "from_photo", lambda *a, **k: a_recipe())
    update = make_photo_update(caption="dinner tonight")
    context = make_photo_context(store, object())

    await bot.on_media(update, context)

    assert store.saved == []
    assert "Braise" in update.message.reply_text.call_args[0][0]
    context.bot.get_file.assert_awaited_once_with("big")


async def test_a_photo_with_no_recipe_says_so(monkeypatch):
    monkeypatch.setattr(bot, "from_photo", lambda *a, **k: None)
    store = FakeStore()
    update = make_photo_update()
    context = make_photo_context(store, object())

    await bot.on_media(update, context)

    assert store.saved == []
    assert "recipe" in update.message.reply_text.call_args[0][0].lower()


async def test_a_pdf_document_reaches_the_extractor_with_its_own_media_type(monkeypatch):
    seen = {}

    def fake_from_photo(images, extractor, vocab, language="en", **kwargs):
        seen["images"] = images
        return a_recipe()

    monkeypatch.setattr(bot, "from_photo", fake_from_photo)
    store = FakeStore()
    update = make_document_update()
    context = make_photo_context(store, object())

    await bot.on_media(update, context)

    assert seen["images"] == [(b"\xff\xd8\xff", "application/pdf")]
    assert store.saved == []
    context.bot.get_file.assert_awaited_once_with("doc")


@pytest.mark.parametrize("mime_type", ["application/zip", "video/mp4", "image/svg+xml", ""])
async def test_a_document_of_an_unreadable_type_is_refused(monkeypatch, mime_type):
    monkeypatch.setattr(bot, "from_photo", lambda *a, **k: a_recipe())
    store = FakeStore()
    update = make_document_update(mime_type=mime_type)
    context = make_photo_context(store, object())

    await bot.on_media(update, context)

    assert store.saved == []
    context.bot.get_file.assert_not_awaited()
    update.message.reply_text.assert_awaited_once_with(t("en", "file_type"))


async def test_a_document_over_the_telegram_limit_is_refused(monkeypatch):
    monkeypatch.setattr(bot, "from_photo", lambda *a, **k: a_recipe())
    store = FakeStore()
    update = make_document_update(file_size=bot.MAX_FILE_BYTES + 1)
    context = make_photo_context(store, object())

    await bot.on_media(update, context)

    assert store.saved == []
    context.bot.get_file.assert_not_awaited()
    update.message.reply_text.assert_awaited_once_with(t("en", "too_big"))


async def test_on_error_replies_to_the_allowed_user():
    update = make_update(text="hi")
    context = make_context(FakeStore(), object())
    context.error = RuntimeError("boom")

    await bot.on_error(update, context)

    update.message.reply_text.assert_awaited_once_with(t("en", "error"))


async def test_on_error_replies_to_a_user_allowed_only_in_the_database():
    update = make_update(text="hi")
    update.effective_user = type("User", (), {"id": 999})()
    context = make_context(FakeStore(), object())
    context.bot_data["users"] = FakeUsers(a_user_record(), allowed={999})
    context.error = RuntimeError("boom")

    await bot.on_error(update, context)

    update.message.reply_text.assert_awaited_once_with(t("en", "error"))


async def test_on_error_stays_silent_for_a_foreign_user():
    update = make_update(text="hi")
    update.effective_user = type("User", (), {"id": 999})()
    context = make_context(FakeStore(), object())
    context.error = RuntimeError("boom")

    await bot.on_error(update, context)

    update.message.reply_text.assert_not_awaited()


PHOTO_HANDLER_FILTER = filters.UpdateType.MESSAGE & (filters.PHOTO | filters.Document.ALL)
TEXT_HANDLER_FILTER = filters.UpdateType.MESSAGE & filters.TEXT & ~filters.COMMAND

CHAT = Chat(id=1, type="private")
NOW = dt.datetime.now(dt.timezone.utc)


def test_a_photo_with_a_caption_matches_the_photo_filter_not_the_text_filter():
    photo = (PhotoSize(file_id="x", file_unique_id="x", width=10, height=10),)
    message = Message(message_id=1, date=NOW, chat=CHAT, photo=photo, caption="yum")
    update = Update(update_id=1, message=message)

    assert PHOTO_HANDLER_FILTER.check_update(update)
    assert not TEXT_HANDLER_FILTER.check_update(update)


def test_a_pdf_document_matches_the_media_filter_not_the_text_filter():
    document = Document(file_id="d", file_unique_id="d", mime_type="application/pdf")
    message = Message(message_id=1, date=NOW, chat=CHAT, document=document, caption="yum")
    update = Update(update_id=1, message=message)

    assert PHOTO_HANDLER_FILTER.check_update(update)
    assert not TEXT_HANDLER_FILTER.check_update(update)


def test_a_plain_text_message_matches_the_text_filter_not_the_photo_filter():
    message = Message(message_id=1, date=NOW, chat=CHAT, text="hello")
    update = Update(update_id=1, message=message)

    assert not PHOTO_HANDLER_FILTER.check_update(update)
    assert TEXT_HANDLER_FILTER.check_update(update)


def test_an_edited_photo_message_matches_neither_handler_filter():
    photo = (PhotoSize(file_id="x", file_unique_id="x", width=10, height=10),)
    message = Message(message_id=1, date=NOW, chat=CHAT, photo=photo, caption="yum")
    update = Update(update_id=1, edited_message=message)

    assert not PHOTO_HANDLER_FILTER.check_update(update)
    assert not TEXT_HANDLER_FILTER.check_update(update)


def test_an_edited_text_message_matches_neither_handler_filter():
    message = Message(message_id=1, date=NOW, chat=CHAT, text="hello")
    update = Update(update_id=1, edited_message=message)

    assert not PHOTO_HANDLER_FILTER.check_update(update)
    assert not TEXT_HANDLER_FILTER.check_update(update)


async def test_start_shows_the_connect_button_when_not_connected(monkeypatch):
    monkeypatch.setattr(bot.callback_server, "start_connect", lambda *a, **k: "https://notion.example/authorize")
    update = make_update(text="/start")
    context = make_context(FakeStore(), object())
    context.bot_data["users"] = FakeUsers(None)

    await bot.on_start(update, context)

    call = update.message.reply_text.call_args
    assert t("en", "connect") in call[0][0]
    buttons = [b for row in call.kwargs["reply_markup"].inline_keyboard for b in row]
    assert buttons[0].url == "https://notion.example/authorize"


async def test_start_gives_the_usual_greeting_when_connected():
    update = make_update(text="/start")
    context = make_context(FakeStore(), object())

    await bot.on_start(update, context)

    assert "recipe" in update.message.reply_text.call_args[0][0].lower()


async def test_a_message_before_connecting_shows_the_connect_button_instead_of_extracting(monkeypatch):
    monkeypatch.setattr(bot.callback_server, "start_connect", lambda *a, **k: "https://notion.example/authorize")
    called = []
    monkeypatch.setattr(bot, "from_url", lambda *a, **k: called.append(1))
    update = make_update(text="https://example.com/recipe")
    context = make_context(FakeStore(), object())
    context.bot_data["users"] = FakeUsers(None)

    await bot.on_text(update, context)

    assert called == []
    assert t("en", "connect") in update.message.reply_text.call_args[0][0]


async def test_disconnect_deletes_the_row_and_confirms():
    update = make_update(text="/disconnect")
    users = FakeUsers(a_user_record())
    context = make_context(FakeStore(), object())
    context.bot_data["users"] = users

    await bot.on_disconnect(update, context)

    assert users.deleted == [1]
    assert "disconnect" in update.message.reply_text.call_args[0][0].lower()


async def test_disconnect_confirms_even_when_the_command_was_an_edit():
    """A CommandHandler also fires on an edited message, where update.message
    is None. The row is already deleted by then, so the reply has to survive."""
    update = make_update(text="/disconnect")
    update.message = None
    users = FakeUsers(a_user_record())
    context = make_context(FakeStore(), object())
    context.bot_data["users"] = users

    await bot.on_disconnect(update, context)

    assert users.deleted == [1]
    assert "disconnect" in update.effective_message.reply_text.call_args[0][0].lower()


async def test_start_answers_a_connected_user_even_when_the_command_was_an_edit():
    update = make_update(text="/start")
    update.message = None
    context = make_context(FakeStore(), object())

    await bot.on_start(update, context)

    assert "recipe link" in update.effective_message.reply_text.call_args[0][0]
