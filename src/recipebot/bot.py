import asyncio
import contextlib
import logging
import re
import uuid
from dataclasses import dataclass, field, replace

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
from .strings import (
    DEFAULT as DEFAULT_LANGUAGE,
    LANGUAGES,
    from_code,
    language_markup,
    t,
)

log = logging.getLogger(__name__)

URL_PATTERN = re.compile(r"https?://\S+")

# The media types both model providers accept. A file outside this set reaches
# the API only to come back a 400, so refuse it here with a useful sentence.
READABLE_MEDIA_TYPES = frozenset(
    {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/gif", "image/heic", "image/heif"}
)
# Telegram's Bot API refuses to serve a file larger than this, so say so before
# the download rather than after it fails.
MAX_FILE_BYTES = 20 * 1024 * 1024

# ponytail: previews live in memory on purpose. A restart forgets them and the
# user re-shares the link. Persist them only if a restart ever loses real work.
# Keyed by (chat_id, message_id) of the preview message. A Telegram message id
# is unique per chat, never across chats, so two allowed users routinely hold
# the same one, and the id alone would match one user's reply to another user's
# preview. The chat_id holds effective_user.id, as it does elsewhere here.
PREVIEWS: dict[tuple[int, int], "Preview"] = {}

# ponytail: reimports live beside the previews, and expire the same way.
REIMPORTS: dict[str, "Reimport"] = {}


def connect_markup(url: str, language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t(language, "connect_button"), url=url)]]
    )


def client_language(user) -> str:
    """What a user who has not chosen yet reads, taken from their Telegram
    client. Their own choice always wins over this."""
    return from_code(getattr(user, "language_code", "") or "")


def language_for(update, context) -> str:
    """The language this user reads, for a path that holds no record already."""
    user = getattr(update, "effective_user", None)
    record = context.bot_data["users"].get(user.id) if user is not None else None
    return record.language if record is not None else client_language(user)


def connect_prompt(
    chat_id: int, cfg: Config, language: str
) -> tuple[str, InlineKeyboardMarkup]:
    """The sentence and the button that start a connect. Three paths send it:
    a first message, a revoked token found mid-save, and a stale language tap."""
    url = callback_server.start_connect(chat_id, cfg, language)
    return t(language, "connect"), connect_markup(url, language)


@dataclass
class Preview:
    recipe: Recipe
    vocab: Vocabulary
    merges: dict[str, str] = field(default_factory=dict)
    # Set when this preview rewrites a page that already exists.
    page_id: str = ""
    language: str = DEFAULT_LANGUAGE


@dataclass
class Reimport:
    page_id: str
    page_url: str
    source_url: str
    corrections: list[str] = field(default_factory=list)
    language: str = DEFAULT_LANGUAGE


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
        record = replace(
            record,
            notion_access_token=tokens.access_token,
            notion_refresh_token=tokens.refresh_token,
        )
        users.save(record)
        try:
            return await asyncio.to_thread(fn, NotionStore.from_user(record))
        except APIResponseError as exc2:
            if exc2.status == 401:
                users.delete(chat_id)
            raise


def write_recipe(
    store, recipe: Recipe, vocab, merges: dict[str, str], page_id: str, language: str
) -> str:
    """Write the recipe and report it in one sentence. A page id rewrites that
    page; without one the store creates a page, or finds the URL already there."""
    if page_id:
        url = store.update_recipe(page_id, recipe, vocab, merges)
        return t(language, "reimported", url=url)
    url, created = store.save_recipe(recipe, vocab, merges)
    return t(language, "saved" if created else "already_saved", url=url)


def preview_text(recipe: Recipe, plan: IngredientPlan, language: str) -> str:
    # Cuisine, meal and difficulty are the Notion database's own select values,
    # so they read in English whatever the user chose; translating them here
    # would disagree with the page the user opens.
    facts = t(language, "preview_facts", time_min=recipe.time_min, servings=recipe.servings)
    if recipe.keeps_days:
        facts += t(language, "preview_keeps", days=recipe.keeps_days)
    lines = [
        recipe.name,
        f"{recipe.cuisine} | {', '.join(recipe.meal)} | {recipe.difficulty}",
        facts,
        "",
        t(language, "preview_ingredients", items=", ".join(i.name for i in recipe.ingredients)),
        t(language, "preview_method", steps=len(recipe.method)),
    ]
    if plan.new:
        lines.append(t(language, "preview_new", items=", ".join(plan.new)))
    for proposed, resembles in plan.near.items():
        lines.append(t(language, "preview_near", proposed=proposed, resembles=resembles))
    if recipe.corrections:
        lines.append(t(language, "preview_corrections", items="; ".join(recipe.corrections)))
    lines.append("")
    lines.append(t(language, "preview_reply"))
    return "\n".join(lines)


def preview_markup(plan: IngredientPlan, language: str) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(t(language, "save_button"), callback_data="save")]
    if plan.near:
        row.append(InlineKeyboardButton(t(language, "merge_button"), callback_data="merge"))
    row.append(InlineKeyboardButton(t(language, "drop_button"), callback_data="drop"))
    return InlineKeyboardMarkup([row])


async def send_preview(recipe, plan, vocab, update, language: str, page_id: str = "") -> None:
    sent = await update.effective_message.reply_text(
        preview_text(recipe, plan, language), reply_markup=preview_markup(plan, language)
    )
    PREVIEWS[(update.effective_user.id, sent.message_id)] = Preview(
        recipe=recipe,
        vocab=vocab,
        merges=dict(plan.near),
        page_id=page_id,
        language=language,
    )


def preview_for_reply(message, chat_id: int) -> tuple[int, int] | None:
    """The PREVIEWS key of the preview the user replied to, or None."""
    reply_to = getattr(message, "reply_to_message", None)
    if reply_to is None:
        return None
    key = (chat_id, reply_to.message_id)
    return key if key in PREVIEWS else None


async def apply_correction(key: tuple[int, int], instruction: str, update, context) -> None:
    preview = PREVIEWS[key]
    corrected = await asyncio.to_thread(
        context.bot_data["extractor"].patch,
        preview.recipe,
        instruction,
        preview.vocab,
        preview.language,
    )
    # Saving or discarding during the model call pops the preview, and editing
    # the message now would paint a dead preview over "Saved: <url>".
    if PREVIEWS.get(key) is not preview:
        await update.message.reply_text(t(preview.language, "expired"))
        return
    if corrected is None:
        await update.message.reply_text(t(preview.language, "no_patch"))
        return
    # Compare before recording the instruction, or the two can never be equal.
    # An instruction that changed nothing must not be stored, both because a
    # reimport would re-apply it forever and because editing a message to its
    # own text is a BadRequest.
    if corrected == preview.recipe:
        await update.message.reply_text(t(preview.language, "no_change"))
        return
    corrected.corrections = [*corrected.corrections, instruction]
    # A correction can add or rename an ingredient, so the plan is stale.
    plan = reconcile_ingredients(preview.vocab, corrected.ingredients)
    preview.recipe = corrected
    preview.merges = dict(plan.near)
    # Edited in place, so the key holds and the user corrects again by
    # replying to the same message.
    await update.message.reply_to_message.edit_text(
        preview_text(corrected, plan, preview.language),
        reply_markup=preview_markup(plan, preview.language),
    )


REIMPORT_ACTIONS = frozenset({"refetch", "stored", "keep"})


def reimport_markup(token: str, language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t(language, "refetch_button"), callback_data=f"refetch:{token}"
                ),
                InlineKeyboardButton(
                    t(language, "stored_button"), callback_data=f"stored:{token}"
                ),
                InlineKeyboardButton(t(language, "keep_button"), callback_data=f"keep:{token}"),
            ]
        ]
    )


async def send_reimport_prompt(page: dict, source_url: str, update, language: str) -> None:
    token = uuid.uuid4().hex
    corrections = corrections_of(page)
    REIMPORTS[token] = Reimport(
        page_id=page["id"],
        page_url=page["url"],
        source_url=source_url,
        corrections=corrections,
        language=language,
    )
    prompt = t(language, "reimport_prompt", url=page["url"])
    if corrections:
        prompt += t(language, "reimport_corrections", corrections="; ".join(corrections))
    await update.effective_message.reply_text(
        prompt, reply_markup=reimport_markup(token, language)
    )


async def on_reimport(action: str, token: str, update, context) -> None:
    query = update.callback_query
    job = REIMPORTS.pop(token, None)
    if job is None:
        await query.edit_message_text(t(language_for(update, context), "expired"))
        return
    language = job.language
    if action == "keep":
        await query.edit_message_text(t(language, "reimport_kept", url=job.page_url))
        return

    chat_id = update.effective_user.id
    extractor = context.bot_data["extractor"]
    await query.edit_message_text(t(language, "reimporting", url=job.page_url))
    vocab = await call_with_reconnect(chat_id, context, lambda store: store.vocabulary())

    if action == "refetch":
        try:
            recipe = await asyncio.to_thread(
                from_url, job.source_url, extractor, vocab, language
            )
        except SocialBlocked:
            await query.message.reply_text(t(language, "blocked"))
            return
    else:
        text = await call_with_reconnect(
            chat_id, context, lambda store: store.source_text(job.page_id)
        )
        if not text.strip():
            await query.message.reply_text(t(language, "no_source_text"))
            return
        recipe = await asyncio.to_thread(
            from_text,
            text,
            extractor,
            vocab,
            language,
            source=source_for(job.source_url),
            source_url=job.source_url,
        )

    if recipe is not None and job.corrections:
        # Carried before the patch, so the corrections reach the page again
        # even when the patch call comes back empty.
        recipe.corrections = job.corrections
        recipe = (
            await asyncio.to_thread(
                extractor.patch, recipe, "\n".join(job.corrections), vocab, language
            )
            or recipe
        )

    await _deliver(recipe, vocab, update, language, page_id=job.page_id)


async def on_language(code: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    users = context.bot_data["users"]
    chat_id = update.effective_user.id
    record = users.get(chat_id)
    if record is None:
        # The row went away between the prompt and the tap, so there is nothing
        # to set the language on. Offer the connect button rather than a dead end.
        text, markup = connect_prompt(chat_id, context.bot_data["cfg"], code)
        await query.edit_message_text(text, reply_markup=markup)
        return
    users.save(replace(record, language=code))
    await query.edit_message_text(t(code, "language_set", language=LANGUAGES[code]))


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action, _, token = query.data.partition(":")

    if action == "lang" and token in LANGUAGES:
        await on_language(token, update, context)
        return

    if action in REIMPORT_ACTIONS:
        await on_reimport(action, token, update, context)
        return

    chat_id = update.effective_user.id
    key = (chat_id, query.message.message_id)
    preview = PREVIEWS.pop(key, None)
    if preview is None:
        await query.edit_message_text(t(language_for(update, context), "expired"))
        return

    if action == "drop":
        await query.edit_message_text(t(preview.language, "discarded"))
        return

    merges = preview.merges if action == "merge" else {}
    try:
        message = await call_with_reconnect(
            chat_id,
            context,
            lambda store: write_recipe(
                store, preview.recipe, preview.vocab, merges, preview.page_id, preview.language
            ),
        )
    except LookupError:
        text, markup = connect_prompt(chat_id, context.bot_data["cfg"], preview.language)
        await query.edit_message_text(text, reply_markup=markup)
        return
    except Exception:
        PREVIEWS[key] = preview
        log.exception("save_recipe failed for %s", key)
        # A second failure re-sends identical text and markup, which Telegram
        # rejects as unmodified. Swallowing it keeps the error handler from
        # posting a second, less useful message on top.
        with contextlib.suppress(BadRequest):
            await query.edit_message_text(
                t(preview.language, "save_failed"),
                reply_markup=preview_markup(
                    IngredientPlan(near=preview.merges), preview.language
                ),
            )
        return
    await query.edit_message_text(message)


async def _deliver(
    recipe: Recipe | None, vocab, update, language: str, page_id: str = ""
) -> None:
    if recipe is None:
        await update.effective_message.reply_text(t(language, "no_recipe"))
        return
    plan = reconcile_ingredients(vocab, recipe.ingredients)
    await send_preview(recipe, plan, vocab, update, language, page_id)


async def send_connect_button(update, context) -> None:
    # Only ever reached where the caller has just read no record for this user,
    # so the language comes off their Telegram client, not a second lookup.
    user = update.effective_user
    text, markup = connect_prompt(
        user.id, context.bot_data["cfg"], client_language(user)
    )
    await update.effective_message.reply_text(text, reply_markup=markup)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("handler failed", exc_info=context.error)
    user = getattr(update, "effective_user", None)
    if user is None or user.id not in context.bot_data["cfg"].allowed_user_ids:
        return
    message = getattr(update, "effective_message", None)
    if message is not None:
        await message.reply_text(t(language_for(update, context), "error"))


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    record = context.bot_data["users"].get(update.effective_user.id)
    if record is None:
        await send_connect_button(update, context)
        return
    await update.message.reply_text(t(record.language, "ready"))


async def on_language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    record = context.bot_data["users"].get(update.effective_user.id)
    if record is None:
        await send_connect_button(update, context)
        return
    await update.message.reply_text(
        t(record.language, "language_prompt"), reply_markup=language_markup()
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_user.id
    record = context.bot_data["users"].get(chat_id)
    if record is None:
        await send_connect_button(update, context)
        return
    language = record.language
    extractor = context.bot_data["extractor"]
    text = update.message.text or ""

    key = preview_for_reply(update.message, chat_id)
    if key is not None:
        await apply_correction(key, text, update, context)
        return

    url = first_url(text)

    if url:
        existing = await call_with_reconnect(chat_id, context, lambda store: store.find_by_url(url))
        if existing:
            await send_reimport_prompt(existing, url, update, language)
            return

    vocab = await call_with_reconnect(chat_id, context, lambda store: store.vocabulary())
    try:
        if url:
            recipe = await asyncio.to_thread(from_url, url, extractor, vocab, language)
        else:
            recipe = await asyncio.to_thread(from_text, text, extractor, vocab, language)
    except SocialBlocked:
        await update.message.reply_text(t(language, "blocked"))
        return

    await _deliver(recipe, vocab, update, language)


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_user.id
    record = context.bot_data["users"].get(chat_id)
    if record is None:
        await send_connect_button(update, context)
        return
    language = record.language
    message = update.message

    if message.photo:
        file_id, media_type = message.photo[-1].file_id, "image/jpeg"
    else:
        document = message.document
        media_type = (document.mime_type or "").lower()
        if media_type not in READABLE_MEDIA_TYPES:
            await message.reply_text(t(language, "file_type"))
            return
        if (document.file_size or 0) > MAX_FILE_BYTES:
            await message.reply_text(t(language, "too_big"))
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
        language,
        caption=message.caption or "",
    )
    await _deliver(recipe, vocab, update, language)


async def on_disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    language = language_for(update, context)
    context.bot_data["users"].delete(update.effective_user.id)
    await update.message.reply_text(t(language, "disconnected"))


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
    application.add_handler(CommandHandler("language", on_language_command))
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
