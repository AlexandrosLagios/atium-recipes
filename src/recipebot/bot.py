import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
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
from .notion import IngredientPlan, NotionStore, Vocabulary, reconcile_ingredients
from .social import SocialBlocked

log = logging.getLogger(__name__)

URL_PATTERN = re.compile(r"https?://\S+")

BLOCKED_MESSAGE = (
    "I could not open that post. Send me a screenshot of it and I will read that instead."
)
NO_RECIPE_MESSAGE = "I could not find a recipe in that."
ERROR_MESSAGE = "Something went wrong handling that. Try again, or send it a different way."

# ponytail: previews live in memory on purpose. A restart forgets them and the
# user re-shares the link. Persist them only if a restart ever loses real work.
PREVIEWS: dict[str, "Preview"] = {}

EXPIRED_MESSAGE = "I no longer have that preview. Share the recipe again."


@dataclass
class Preview:
    recipe: Recipe
    vocab: Vocabulary
    merges: dict[str, str] = field(default_factory=dict)


def first_url(text: str) -> str:
    match = URL_PATTERN.search(text or "")
    return match.group() if match else ""


def make_gate(allowed_user_id: int):
    async def gate(update, context) -> None:
        seen_id = "unknown"
        try:
            user = getattr(update, "effective_user", None)
            if user is not None:
                seen_id = user.id
            allowed = seen_id == allowed_user_id
        except Exception:
            allowed = False
        if not allowed:
            log.warning("dropped update from %s", seen_id)
            raise ApplicationHandlerStop

    return gate


async def handle_recipe(recipe: Recipe, store, vocab, update) -> None:
    plan = reconcile_ingredients(vocab, recipe.ingredients)
    if recipe.high_confidence and not plan.near:
        url = await asyncio.to_thread(store.save_recipe, recipe, vocab)
        await update.message.reply_text(f"Saved: {url}")
        return
    await send_preview(recipe, plan, vocab, update)


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


async def send_preview(recipe, plan, vocab, update) -> None:
    token = uuid.uuid4().hex
    PREVIEWS[token] = Preview(recipe=recipe, vocab=vocab, merges=dict(plan.near))
    await update.message.reply_text(
        preview_text(recipe, plan), reply_markup=preview_markup(token, plan)
    )


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


async def _deliver(recipe_or_none, store, vocab, update) -> None:
    if recipe_or_none is None:
        await update.message.reply_text(NO_RECIPE_MESSAGE)
        return
    await handle_recipe(recipe_or_none, store, vocab, update)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("handler failed", exc_info=context.error)
    user = getattr(update, "effective_user", None)
    if user is None or user.id != context.bot_data["allowed_user_id"]:
        return
    message = getattr(update, "effective_message", None)
    if message is not None:
        await message.reply_text(ERROR_MESSAGE)


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
    application.bot_data["allowed_user_id"] = cfg.allowed_user_id

    application.add_handler(
        TypeHandler(Update, make_gate(cfg.allowed_user_id), block=True), group=-1
    )
    application.add_error_handler(on_error)
    application.add_handler(CommandHandler("start", on_start))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.UpdateType.MESSAGE & filters.PHOTO, on_photo))
    application.add_handler(
        MessageHandler(filters.UpdateType.MESSAGE & filters.TEXT & ~filters.COMMAND, on_text)
    )
    return application
