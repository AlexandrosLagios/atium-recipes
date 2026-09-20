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
from .extract import from_photo, from_text, from_url, source_for
from .llm import Extractor
from .models import Recipe
from .notion import (
    IngredientPlan,
    NotionStore,
    Vocabulary,
    corrections_of,
    reconcile_ingredients,
)
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

# ponytail: reimports live beside the previews, and expire the same way.
REIMPORTS: dict[str, "Reimport"] = {}

EXPIRED_MESSAGE = "I no longer have that preview. Share the recipe again."
NO_PATCH_MESSAGE = (
    'I could not apply that. Try naming the field, for example "servings is 2".'
)
NO_CHANGE_MESSAGE = "That changed nothing."
NO_SOURCE_TEXT_MESSAGE = (
    "That page has no saved source text. Tap Refetch link to read the site again."
)

CONNECT_MESSAGE = "Connect your Notion account to save recipes there."


def connect_markup(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Connect Notion", url=url)]])


@dataclass
class Preview:
    recipe: Recipe
    vocab: Vocabulary
    merges: dict[str, str] = field(default_factory=dict)
    # Set when this preview rewrites a page that already exists.
    page_id: str = ""
    # The message the user replies to in order to correct this preview, and
    # the sender it belongs to. A Telegram message id is unique per chat, never
    # across chats, so two allowed users routinely hold the same one, and the
    # id alone would match one user's reply to another user's preview. Holds
    # effective_user.id, which is what the rest of this module calls chat_id.
    chat_id: int = 0
    message_id: int = 0


@dataclass
class Reimport:
    page_id: str
    page_url: str
    source_url: str
    corrections: list[str] = field(default_factory=list)


def first_url(text: str) -> str:
    match = URL_PATTERN.search(text or "")
    return match.group() if match else ""


def make_gate(allowed_user_ids: frozenset[int], users):
    """The environment ids are checked first and never touch the database, so
    an unreadable database locks out the runtime ids but never the owner."""

    async def gate(update, context) -> None:
        seen_id = "unknown"
        try:
            user = getattr(update, "effective_user", None)
            if user is not None:
                seen_id = user.id
            allowed = seen_id in allowed_user_ids or seen_id in users.allowed_ids()
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


def write_recipe(store, recipe: Recipe, vocab, merges: dict[str, str], page_id: str) -> str:
    """Write the recipe and report it in one sentence. A page id rewrites that
    page; without one the store creates a page, or finds the URL already there."""
    if page_id:
        return f"Reimported: {store.update_recipe(page_id, recipe, vocab, merges)}"
    url, created = store.save_recipe(recipe, vocab, merges)
    return f"Saved: {url}" if created else f"Already saved: {url}"


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
    if recipe.corrections:
        lines.append("Corrections: " + "; ".join(recipe.corrections))
    lines.append("")
    lines.append("Reply to this message to correct it.")
    return "\n".join(lines)


def preview_markup(token: str, plan: IngredientPlan) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton("Save", callback_data=f"save:{token}")]
    if plan.near:
        row.append(InlineKeyboardButton("Save and merge", callback_data=f"merge:{token}"))
    row.append(InlineKeyboardButton("Discard", callback_data=f"drop:{token}"))
    return InlineKeyboardMarkup([row])


async def send_preview(recipe, plan, vocab, update, page_id: str = "") -> None:
    token = uuid.uuid4().hex
    preview = Preview(
        recipe=recipe,
        vocab=vocab,
        merges=dict(plan.near),
        page_id=page_id,
        chat_id=update.effective_user.id,
    )
    PREVIEWS[token] = preview
    sent = await update.effective_message.reply_text(
        preview_text(recipe, plan), reply_markup=preview_markup(token, plan)
    )
    preview.message_id = sent.message_id


def preview_for_reply(message, chat_id: int) -> str:
    """The token of the preview the user replied to, or "". Scanning the
    handful of live previews beats a second dict keyed by message id, which
    would leak an entry on every Discard."""
    reply_to = getattr(message, "reply_to_message", None)
    if reply_to is None:
        return ""
    target = (chat_id, reply_to.message_id)
    return next(
        (
            token
            for token, preview in PREVIEWS.items()
            if (preview.chat_id, preview.message_id) == target
        ),
        "",
    )


async def apply_correction(token: str, instruction: str, update, context) -> None:
    preview = PREVIEWS[token]
    corrected = await asyncio.to_thread(
        context.bot_data["extractor"].patch,
        preview.recipe,
        instruction,
        preview.vocab,
    )
    # Saving or discarding during the model call pops the token, and editing
    # the message now would paint a dead preview over "Saved: <url>".
    if PREVIEWS.get(token) is not preview:
        await update.message.reply_text(EXPIRED_MESSAGE)
        return
    if corrected is None:
        await update.message.reply_text(NO_PATCH_MESSAGE)
        return
    # Compare before recording the instruction, or the two can never be equal.
    # An instruction that changed nothing must not be stored, both because a
    # reimport would re-apply it forever and because editing a message to its
    # own text is a BadRequest.
    if corrected == preview.recipe:
        await update.message.reply_text(NO_CHANGE_MESSAGE)
        return
    corrected.corrections = [*corrected.corrections, instruction]
    # A correction can add or rename an ingredient, so the plan is stale.
    plan = reconcile_ingredients(preview.vocab, corrected.ingredients)
    preview.recipe = corrected
    preview.merges = dict(plan.near)
    # Edited in place, so the token and the message id hold and the user
    # corrects again by replying to the same message.
    await context.bot.edit_message_text(
        preview_text(corrected, plan),
        chat_id=update.effective_user.id,
        message_id=preview.message_id,
        reply_markup=preview_markup(token, plan),
    )


REIMPORT_ACTIONS = frozenset({"refetch", "stored", "keep"})


def reimport_markup(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Refetch link", callback_data=f"refetch:{token}"),
                InlineKeyboardButton("Reuse saved text", callback_data=f"stored:{token}"),
                InlineKeyboardButton("Keep", callback_data=f"keep:{token}"),
            ]
        ]
    )


async def send_reimport_prompt(page: dict, source_url: str, update) -> None:
    token = uuid.uuid4().hex
    corrections = corrections_of(page)
    REIMPORTS[token] = Reimport(
        page_id=page["id"],
        page_url=page["url"],
        source_url=source_url,
        corrections=corrections,
    )
    prompt = (
        f"Already saved: {page['url']}\n\nReimport it? Refetch link reads the site again. "
        "Reuse saved text runs the extraction over the text already on the page. "
        "Both replace the page body, so anything you wrote there by hand goes."
    )
    if corrections:
        prompt += "\n\nBoth also apply your corrections again: " + "; ".join(corrections)
    await update.effective_message.reply_text(
        prompt, reply_markup=reimport_markup(token)
    )


async def on_reimport(action: str, token: str, update, context) -> None:
    query = update.callback_query
    job = REIMPORTS.pop(token, None)
    if job is None:
        await query.edit_message_text(EXPIRED_MESSAGE)
        return
    if action == "keep":
        await query.edit_message_text(f"Left as it is: {job.page_url}")
        return

    chat_id = update.effective_user.id
    extractor = context.bot_data["extractor"]
    await query.edit_message_text(f"Reimporting {job.page_url}")
    vocab = await call_with_reconnect(chat_id, context, lambda store: store.vocabulary())

    if action == "refetch":
        try:
            recipe = await asyncio.to_thread(from_url, job.source_url, extractor, vocab)
        except SocialBlocked:
            await query.message.reply_text(BLOCKED_MESSAGE)
            return
    else:
        text = await call_with_reconnect(
            chat_id, context, lambda store: store.source_text(job.page_id)
        )
        if not text.strip():
            await query.message.reply_text(NO_SOURCE_TEXT_MESSAGE)
            return
        recipe = await asyncio.to_thread(
            from_text,
            text,
            extractor,
            vocab,
            source=source_for(job.source_url),
            source_url=job.source_url,
        )

    if recipe is not None and job.corrections:
        # Carried before the patch, so the corrections reach the page again
        # even when the patch call comes back empty.
        recipe.corrections = job.corrections
        recipe = (
            await asyncio.to_thread(
                extractor.patch, recipe, "\n".join(job.corrections), vocab
            )
            or recipe
        )

    await _deliver(recipe, vocab, update, page_id=job.page_id)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action, _, token = query.data.partition(":")

    if action in REIMPORT_ACTIONS:
        await on_reimport(action, token, update, context)
        return

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
        message = await call_with_reconnect(
            chat_id,
            context,
            lambda store: write_recipe(
                store, preview.recipe, preview.vocab, merges, preview.page_id
            ),
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
    await query.edit_message_text(message)


async def _deliver(recipe: Recipe | None, vocab, update, page_id: str = "") -> None:
    if recipe is None:
        await update.effective_message.reply_text(NO_RECIPE_MESSAGE)
        return
    plan = reconcile_ingredients(vocab, recipe.ingredients)
    await send_preview(recipe, plan, vocab, update, page_id)


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

    token = preview_for_reply(update.message, chat_id)
    if token:
        await apply_correction(token, text, update, context)
        return

    url = first_url(text)

    if url:
        existing = await call_with_reconnect(chat_id, context, lambda store: store.find_by_url(url))
        if existing:
            await send_reimport_prompt(existing, url, update)
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

    await _deliver(recipe, vocab, update)


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
    await _deliver(recipe, vocab, update)


async def on_disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.bot_data["users"].delete(update.effective_user.id)
    await update.message.reply_text("Disconnected. Send me a message to connect a Notion account again.")


OWNER_ONLY_MESSAGE = "That command is for the bot owner only."
BAD_ID_MESSAGE = "Send a numeric Telegram user id, like /allow 6529645381."


def _is_owner(update, context) -> bool:
    return update.effective_user.id == context.bot_data["cfg"].owner_id


def _target_id(context) -> int | None:
    raw = (context.args or [""])[0]
    return int(raw) if raw.isdigit() else None


async def on_allow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update, context):
        await update.message.reply_text(OWNER_ONLY_MESSAGE)
        return
    target = _target_id(context)
    if target is None:
        await update.message.reply_text(BAD_ID_MESSAGE)
        return
    cfg = context.bot_data["cfg"]
    users = context.bot_data["users"]
    if target in cfg.allowed_user_ids or target in users.allowed_ids():
        await update.message.reply_text(f"{target} is already allowed.")
        return
    users.allow(target)
    log.info("owner %s allowed %s", update.effective_user.id, target)
    await update.message.reply_text(f"Allowed {target}. They can message the bot now.")


async def on_deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update, context):
        await update.message.reply_text(OWNER_ONLY_MESSAGE)
        return
    target = _target_id(context)
    if target is None:
        await update.message.reply_text(BAD_ID_MESSAGE)
        return
    cfg = context.bot_data["cfg"]
    users = context.bot_data["users"]
    if target == cfg.owner_id:
        await update.message.reply_text("That is the owner id. Refusing to lock you out.")
        return
    if target in cfg.allowed_user_ids:
        await update.message.reply_text(
            f"{target} comes from TELEGRAM_ALLOWED_USER_IDS. "
            "Remove it there and restart the bot."
        )
        return
    if target not in users.allowed_ids():
        await update.message.reply_text(f"{target} is not allowed.")
        return
    users.deny(target)
    log.info("owner %s denied %s", update.effective_user.id, target)
    await update.message.reply_text(f"Denied {target}.")


async def on_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update, context):
        await update.message.reply_text(OWNER_ONLY_MESSAGE)
        return
    cfg = context.bot_data["cfg"]
    users = context.bot_data["users"]
    lines = [f"{user_id} (env)" for user_id in sorted(cfg.allowed_user_ids)]
    lines += [str(user_id) for user_id in sorted(users.allowed_ids() - cfg.allowed_user_ids)]
    await update.message.reply_text("Allowed users:\n" + "\n".join(lines))


def build_application(cfg: Config, users, extractor: Extractor, *, post_init=None) -> Application:
    builder = Application.builder().token(cfg.telegram_token)
    if post_init is not None:
        builder = builder.post_init(post_init)
    application = builder.build()
    application.bot_data["cfg"] = cfg
    application.bot_data["users"] = users
    application.bot_data["extractor"] = extractor

    application.add_handler(
        TypeHandler(Update, make_gate(cfg.allowed_user_ids, users), block=True), group=-1
    )
    application.add_error_handler(on_error)
    application.add_handler(CommandHandler("start", on_start))
    application.add_handler(CommandHandler("disconnect", on_disconnect))
    application.add_handler(CommandHandler("allow", on_allow))
    application.add_handler(CommandHandler("deny", on_deny))
    application.add_handler(CommandHandler("allowed", on_allowed))
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
