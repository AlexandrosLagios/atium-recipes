import asyncio
import contextlib
import logging
import re
import uuid
from dataclasses import dataclass, field

from notion_client.errors import APIResponseError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
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

from . import callback_server, oauth
from .config import Config
from .extract import from_photo, from_text, from_url
from .llm import Extractor
from .models import Recipe
from .notion import IngredientPlan, NotionStore, Vocabulary, reconcile_ingredients
from .social import SocialBlocked
from .users import UserRecord

log = logging.getLogger(__name__)

URL_PATTERN = re.compile(r"https?://\S+")

BLOCKED_MESSAGE = (
    "I could not open that post. Send me a screenshot of it and I will read that instead."
)
NO_RECIPE_MESSAGE = "I could not find a recipe in that."
# The media types both model providers accept. A file outside this set reaches
# the API only to come back a 400, so refuse it here with a useful sentence.
READABLE_MEDIA_TYPES = frozenset(
    {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/gif", "image/heic", "image/heif"}
)
FILE_TYPE_MESSAGE = (
    "I can read a PDF or an image file. Send one of those, a photo, or paste the text."
)
# Telegram's Bot API refuses to serve a file larger than this, so say so before
# the download rather than after it fails.
MAX_FILE_BYTES = 20 * 1024 * 1024
TOO_BIG_MESSAGE = "That file is over 20 MB, which Telegram will not hand me. Send a smaller one."
ERROR_MESSAGE = "Something went wrong handling that. Try again, or send it a different way."

# ponytail: previews live in memory on purpose. A restart forgets them and the
# user re-shares the link. Persist them only if a restart ever loses real work.
PREVIEWS: dict[str, "Preview"] = {}

EXPIRED_MESSAGE = "I no longer have that preview. Share the recipe again."

CONNECT_MESSAGE = "Connect your Notion account to save recipes there."


def connect_markup(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Connect Notion", url=url)]])


@dataclass
class Preview:
    recipe: Recipe
    vocab: Vocabulary
    merges: dict[str, str] = field(default_factory=dict)


def first_url(text: str) -> str:
    match = URL_PATTERN.search(text or "")
    return match.group() if match else ""


def make_gate(allowed_user_ids: frozenset[int]):
    async def gate(update, context) -> None:
        seen_id = "unknown"
        try:
            user = getattr(update, "effective_user", None)
            if user is not None:
                seen_id = user.id
            allowed = seen_id in allowed_user_ids
        except Exception:
            allowed = False
        if not allowed:
            log.warning("dropped update from %s", seen_id)
            raise ApplicationHandlerStop

    return gate


async def call_with_reconnect(chat_id: int, context, fn):
    """Run fn(store) in a thread. On a revoked connection (401), refresh the
    token once and retry; a second failure drops the row, so the next
    message from this chat offers Connect again."""
    cfg = context.bot_data["cfg"]
    users = context.bot_data["users"]
    record = users.get(chat_id)
    if record is None:
        raise LookupError(chat_id)
    try:
        return await asyncio.to_thread(fn, NotionStore.from_user(record))
    except APIResponseError as exc:
        if exc.status != 401 or not record.notion_refresh_token:
            if exc.status == 401:
                users.delete(chat_id)
            raise
        tokens = await asyncio.to_thread(
            oauth.refresh_access_token, cfg.notion_client_id, cfg.notion_client_secret, record.notion_refresh_token
        )
        record = UserRecord(
            telegram_user_id=record.telegram_user_id,
            notion_access_token=tokens.access_token,
            notion_refresh_token=tokens.refresh_token,
            recipes_ds=record.recipes_ds,
            ingredients_ds=record.ingredients_ds,
            workspace_name=record.workspace_name,
            connected_at=record.connected_at,
        )
        users.save(record)
        try:
            return await asyncio.to_thread(fn, NotionStore.from_user(record))
        except APIResponseError as exc2:
            if exc2.status == 401:
                users.delete(chat_id)
            raise


async def handle_recipe(recipe: Recipe, chat_id: int, context, vocab, update) -> None:
    plan = reconcile_ingredients(vocab, recipe.ingredients)
    if recipe.high_confidence and not plan.near:
        url, created = await call_with_reconnect(
            chat_id, context, lambda store: store.save_recipe(recipe, vocab)
        )
        message = f"Saved: {url}" if created else f"Already saved: {url}"
        await update.message.reply_text(message)
        return
    await send_preview(recipe, plan, vocab, update)


def preview_text(recipe: Recipe, plan: IngredientPlan) -> str:
    lines = [
        recipe.name,
        f"{recipe.cuisine} | {', '.join(recipe.meal)} | {recipe.difficulty}",
        f"{recipe.time_min} min | {recipe.servings} servings" + (f" | keeps {recipe.keeps_days}d" if recipe.keeps_days else ""),
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
    chat_id = update.effective_user.id
    try:
        url, created = await call_with_reconnect(
            chat_id, context, lambda store: store.save_recipe(preview.recipe, preview.vocab, merges)
        )
    except LookupError:
        url = callback_server.start_connect(chat_id, context.bot_data["cfg"])
        await query.edit_message_text(CONNECT_MESSAGE, reply_markup=connect_markup(url))
        return
    except Exception:
        PREVIEWS[token] = preview
        log.exception("save_recipe failed for token %s", token)
        # A second failure re-sends identical text and markup, which Telegram
        # rejects as unmodified. Swallowing it keeps the error handler from
        # posting a second, less useful message on top.
        with contextlib.suppress(BadRequest):
            await query.edit_message_text(
                "Saving failed. Tap Save to try again.",
                reply_markup=preview_markup(token, IngredientPlan(near=preview.merges)),
            )
        return
    message = f"Saved: {url}" if created else f"Already saved: {url}"
    await query.edit_message_text(message)


async def _deliver(recipe_or_none, chat_id: int, context, vocab, update) -> None:
    if recipe_or_none is None:
        await update.message.reply_text(NO_RECIPE_MESSAGE)
        return
    await handle_recipe(recipe_or_none, chat_id, context, vocab, update)


async def send_connect_button(update, context) -> None:
    chat_id = update.effective_user.id
    url = callback_server.start_connect(chat_id, context.bot_data["cfg"])
    await update.effective_message.reply_text(CONNECT_MESSAGE, reply_markup=connect_markup(url))


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("handler failed", exc_info=context.error)
    user = getattr(update, "effective_user", None)
    if user is None or user.id not in context.bot_data["cfg"].allowed_user_ids:
        return
    message = getattr(update, "effective_message", None)
    if message is not None:
        await message.reply_text(ERROR_MESSAGE)


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    users = context.bot_data["users"]
    if users.get(update.effective_user.id) is None:
        await send_connect_button(update, context)
        return
    await update.message.reply_text(
        "Send me a recipe link, an Instagram or TikTok post, a photo, a PDF, or pasted text."
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_user.id
    users = context.bot_data["users"]
    if users.get(chat_id) is None:
        await send_connect_button(update, context)
        return
    extractor = context.bot_data["extractor"]
    text = update.message.text or ""
    url = first_url(text)

    if url:
        existing = await call_with_reconnect(chat_id, context, lambda store: store.find_by_url(url))
        if existing:
            await update.message.reply_text(f"Already saved: {existing}")
            return

    vocab = await call_with_reconnect(chat_id, context, lambda store: store.vocabulary())
    try:
        if url:
            recipe = await asyncio.to_thread(from_url, url, extractor, vocab)
        else:
            recipe = await asyncio.to_thread(from_text, text, extractor, vocab)
    except SocialBlocked:
        await update.message.reply_text(BLOCKED_MESSAGE)
        return

    await _deliver(recipe, chat_id, context, vocab, update)


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_user.id
    users = context.bot_data["users"]
    if users.get(chat_id) is None:
        await send_connect_button(update, context)
        return
    message = update.message

    if message.photo:
        file_id, media_type = message.photo[-1].file_id, "image/jpeg"
    else:
        document = message.document
        media_type = (document.mime_type or "").lower()
        if media_type not in READABLE_MEDIA_TYPES:
            await message.reply_text(FILE_TYPE_MESSAGE)
            return
        if (document.file_size or 0) > MAX_FILE_BYTES:
            await message.reply_text(TOO_BIG_MESSAGE)
            return
        file_id = document.file_id

    telegram_file = await context.bot.get_file(file_id)
    data = bytes(await telegram_file.download_as_bytearray())

    vocab = await call_with_reconnect(chat_id, context, lambda store: store.vocabulary())
    recipe = await asyncio.to_thread(
        from_photo,
        [(data, media_type)],
        context.bot_data["extractor"],
        vocab,
        caption=message.caption or "",
    )
    await _deliver(recipe, chat_id, context, vocab, update)


async def on_disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.bot_data["users"].delete(update.effective_user.id)
    await update.message.reply_text("Disconnected. Send me a message to connect a Notion account again.")


def build_application(cfg: Config, users, extractor: Extractor, *, post_init=None) -> Application:
    builder = Application.builder().token(cfg.telegram_token)
    if post_init is not None:
        builder = builder.post_init(post_init)
    application = builder.build()
    application.bot_data["cfg"] = cfg
    application.bot_data["users"] = users
    application.bot_data["extractor"] = extractor

    application.add_handler(
        TypeHandler(Update, make_gate(cfg.allowed_user_ids), block=True), group=-1
    )
    application.add_error_handler(on_error)
    application.add_handler(CommandHandler("start", on_start))
    application.add_handler(CommandHandler("disconnect", on_disconnect))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & (filters.PHOTO | filters.Document.ALL), on_media
        )
    )
    application.add_handler(
        MessageHandler(filters.UpdateType.MESSAGE & filters.TEXT & ~filters.COMMAND, on_text)
    )
    return application
