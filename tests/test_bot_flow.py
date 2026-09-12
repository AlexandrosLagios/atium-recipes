import datetime as dt
from unittest.mock import AsyncMock

from telegram import Chat, Message, PhotoSize, Update
from telegram.ext import filters

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


async def test_a_near_match_forces_the_preview_even_when_high_confidence(monkeypatch):
    store = FakeStore()
    near_match = a_recipe(ingredients=[Ingredient(name="Chiken")])
    monkeypatch.setattr(bot, "from_url", lambda *a, **k: near_match)
    update = make_update(text="https://redhousespice.com/x/")

    await bot.on_text(update, make_context(store, object()))

    assert store.saved == []
    assert "Preview pending" in update.message.reply_text.call_args[0][0]


def make_photo_update(caption=""):
    update = type("U", (), {})()
    update.effective_user = type("User", (), {"id": 1})()
    update.message = type("M", (), {})()
    update.message.text = None
    update.message.caption = caption
    update.message.photo = [type("P", (), {"file_id": "small"})(), type("P", (), {"file_id": "big"})()]
    update.message.reply_text = AsyncMock()
    return update


def make_photo_context(store, extractor, data=b"\xff\xd8\xff"):
    telegram_file = type("F", (), {})()
    telegram_file.download_as_bytearray = AsyncMock(return_value=bytearray(data))
    context = type("C", (), {})()
    context.bot_data = {"store": store, "extractor": extractor}
    context.bot = type("B", (), {"get_file": AsyncMock(return_value=telegram_file)})()
    return context


async def test_a_high_confidence_photo_recipe_writes_at_once(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(bot, "from_photo", lambda *a, **k: a_recipe())
    update = make_photo_update(caption="dinner tonight")
    context = make_photo_context(store, object())

    await bot.on_photo(update, context)

    assert len(store.saved) == 1
    assert "https://notion.so/new" in update.message.reply_text.call_args[0][0]
    context.bot.get_file.assert_awaited_once_with("big")


async def test_a_photo_with_no_recipe_says_so(monkeypatch):
    monkeypatch.setattr(bot, "from_photo", lambda *a, **k: None)
    store = FakeStore()
    update = make_photo_update()
    context = make_photo_context(store, object())

    await bot.on_photo(update, context)

    assert store.saved == []
    assert "recipe" in update.message.reply_text.call_args[0][0].lower()


CHAT = Chat(id=1, type="private")
NOW = dt.datetime.now(dt.timezone.utc)


def test_a_photo_with_a_caption_matches_the_photo_filter_not_the_text_filter():
    photo = (PhotoSize(file_id="x", file_unique_id="x", width=10, height=10),)
    message = Message(message_id=1, date=NOW, chat=CHAT, photo=photo, caption="yum")
    update = Update(update_id=1, message=message)

    assert filters.PHOTO.check_update(update)
    assert not (filters.TEXT & ~filters.COMMAND).check_update(update)


def test_a_plain_text_message_matches_the_text_filter_not_the_photo_filter():
    message = Message(message_id=1, date=NOW, chat=CHAT, text="hello")
    update = Update(update_id=1, message=message)

    assert not filters.PHOTO.check_update(update)
    assert (filters.TEXT & ~filters.COMMAND).check_update(update)
