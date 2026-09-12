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
