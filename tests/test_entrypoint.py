from telegram.ext import CallbackQueryHandler, TypeHandler

from recipebot.bot import build_application
from recipebot.config import Config

CFG = Config(
    telegram_token="123:abc",
    allowed_user_id=12345,
    notion_token="ntn",
    recipes_ds="ds-r",
    ingredients_ds="ds-i",
    anthropic_key="sk-ant",
)


def test_the_gate_runs_before_every_other_handler():
    application = build_application(CFG, object(), object())

    groups = sorted(application.handlers)
    assert groups[0] == -1
    assert isinstance(application.handlers[-1][0], TypeHandler)


def test_the_callback_handler_is_registered():
    application = build_application(CFG, object(), object())

    assert any(
        isinstance(handler, CallbackQueryHandler) for handler in application.handlers[0]
    )


import pytest

from recipebot import __main__ as entrypoint


def test_main_refuses_to_start_without_the_environment(monkeypatch):
    for name in ("TELEGRAM_TOKEN", "TELEGRAM_ALLOWED_USER_ID", "NOTION_TOKEN",
                 "NOTION_RECIPES_DS", "NOTION_INGREDIENTS_DS", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="TELEGRAM_TOKEN"):
        entrypoint.main()
