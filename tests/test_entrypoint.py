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


import asyncio

import pytest

from recipebot import __main__ as entrypoint


def test_main_refuses_to_start_without_the_environment(monkeypatch):
    for name in ("TELEGRAM_TOKEN", "TELEGRAM_ALLOWED_USER_IDS", "NOTION_CLIENT_ID",
                 "NOTION_CLIENT_SECRET", "NOTION_REDIRECT_URI", "LLM_PROVIDER",
                 "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="TELEGRAM_TOKEN"):
        entrypoint.main()


from unittest.mock import AsyncMock


async def test_post_init_starts_the_callback_server_with_the_configured_port(monkeypatch):
    import recipebot.__main__ as entrypoint

    captured = {}

    def fake_make_server(cfg, users, fixture, notify):
        captured["port"] = cfg.oauth_callback_port
        return object()

    monkeypatch.setattr(entrypoint, "make_server", fake_make_server)
    monkeypatch.setattr(entrypoint, "run_in_background", lambda server: None)

    cfg = Config(
        telegram_token="tok",
        allowed_user_ids=frozenset({1}),
        notion_client_id="c",
        notion_client_secret="s",
        notion_redirect_uri="https://bot.example/oauth/callback",
        oauth_callback_port=9090,
        db_path=":memory:",
        llm_provider="gemini",
        llm_api_key="g-key",
    )
    post_init = entrypoint._build_post_init(cfg, users=object(), fixture={})
    application = type("A", (), {"bot": type("B", (), {"send_message": AsyncMock()})()})()

    await post_init(application)

    assert captured["port"] == 9090


async def test_post_init_notify_sends_a_telegram_message_on_the_running_loop(monkeypatch):
    import recipebot.__main__ as entrypoint

    captured = {}

    def fake_make_server(cfg, users, fixture, notify):
        captured["notify"] = notify
        return object()

    monkeypatch.setattr(entrypoint, "make_server", fake_make_server)
    monkeypatch.setattr(entrypoint, "run_in_background", lambda server: None)

    cfg = Config(
        telegram_token="tok",
        allowed_user_ids=frozenset({1}),
        notion_client_id="c",
        notion_client_secret="s",
        notion_redirect_uri="https://bot.example/oauth/callback",
        oauth_callback_port=9090,
        db_path=":memory:",
        llm_provider="gemini",
        llm_api_key="g-key",
    )
    post_init = entrypoint._build_post_init(cfg, users=object(), fixture={})
    send_message = AsyncMock()
    application = type("A", (), {"bot": type("B", (), {"send_message": send_message})()})()

    await post_init(application)
    captured["notify"](42, "hello")
    # run_coroutine_threadsafe schedules via call_soon_threadsafe, which needs
    # its own loop turn before the Task it creates gets to run; a bare
    # sleep(0) yields only once, so give it a real tick instead of a flaky one.
    await asyncio.sleep(0.01)

    send_message.assert_awaited_once_with(42, "hello", reply_markup=None)
