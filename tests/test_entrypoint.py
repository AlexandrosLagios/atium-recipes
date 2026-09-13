from telegram.ext import CallbackQueryHandler, TypeHandler

from recipebot.bot import build_application
from recipebot.config import Config

CFG = Config(
    telegram_token="123:abc",
    allowed_user_ids=frozenset({12345}),
    notion_client_id="c",
    notion_client_secret="s",
    notion_redirect_uri="https://bot.example/oauth/callback",
    oauth_callback_port=8080,
    db_path=":memory:",
    llm_provider="gemini",
    llm_api_key="g-key",
)


def test_the_gate_runs_before_every_other_handler():
    application = build_application(CFG, object(), object())

    groups = sorted(application.handlers)
    assert groups[0] == -1
    assert isinstance(application.handlers[-1][0], TypeHandler)
    assert application.handlers[-1][0].block is True


def test_the_callback_handler_is_registered():
    application = build_application(CFG, object(), object())

    assert any(
        isinstance(handler, CallbackQueryHandler) for handler in application.handlers[0]
    )


import pytest

from recipebot import __main__ as entrypoint


def test_main_refuses_to_start_without_the_environment(monkeypatch):
    for name in ("TELEGRAM_TOKEN", "TELEGRAM_ALLOWED_USER_IDS", "NOTION_CLIENT_ID",
                 "NOTION_CLIENT_SECRET", "NOTION_REDIRECT_URI", "LLM_PROVIDER",
                 "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="TELEGRAM_TOKEN"):
        entrypoint.main()
