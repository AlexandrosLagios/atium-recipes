import httpx
import pytest
from notion_client.errors import APIResponseError

from recipebot import bot
from recipebot.config import Config
from recipebot.users import UserRecord


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
    def __init__(self, record=None):
        self.record = record
        self.deleted = []

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


def a_context(users) -> object:
    context = type("C", (), {})()
    context.bot_data = {"cfg": a_config(), "users": users}
    return context


def _api_error(status):
    # notion-client 3.1.0's APIResponseError takes the parsed fields
    # directly, not an httpx.Response, so build it from those.
    return APIResponseError(
        code="unauthorized" if status == 401 else "internal_server_error",
        status=status,
        message="revoked",
        headers=httpx.Headers(),
        raw_body_text="",
    )


async def test_call_with_reconnect_returns_the_function_result():
    context = a_context(FakeUsers(a_user_record()))

    result = await bot.call_with_reconnect(1, context, lambda store: "ok")

    assert result == "ok"


async def test_call_with_reconnect_refreshes_once_on_a_401(monkeypatch):
    users = FakeUsers(a_user_record(language="el"))
    context = a_context(users)
    monkeypatch.setattr(
        bot.oauth,
        "refresh_access_token",
        lambda *a, **k: bot.oauth.TokenPair(access_token="tok-2", refresh_token="refresh-2", workspace_name="Kitchen"),
    )
    calls = []

    def fn(store):
        calls.append(store)
        if len(calls) == 1:
            raise _api_error(401)
        return "ok"

    result = await bot.call_with_reconnect(1, context, fn)

    assert result == "ok"
    assert users.record.notion_access_token == "tok-2"
    assert users.record.language == "el"


async def test_call_with_reconnect_drops_the_user_after_a_second_401(monkeypatch):
    users = FakeUsers(a_user_record())
    context = a_context(users)
    monkeypatch.setattr(
        bot.oauth,
        "refresh_access_token",
        lambda *a, **k: bot.oauth.TokenPair(access_token="tok-2", refresh_token="refresh-2", workspace_name="Kitchen"),
    )

    def fn(store):
        raise _api_error(401)

    with pytest.raises(APIResponseError):
        await bot.call_with_reconnect(1, context, fn)

    assert users.deleted == [1]


async def test_call_with_reconnect_re_raises_a_non_401_without_dropping_the_user():
    users = FakeUsers(a_user_record())
    context = a_context(users)

    def fn(store):
        raise _api_error(500)

    with pytest.raises(APIResponseError):
        await bot.call_with_reconnect(1, context, fn)

    assert users.deleted == []


async def test_call_with_reconnect_raises_lookuperror_for_an_unknown_chat():
    context = a_context(FakeUsers(None))

    with pytest.raises(LookupError):
        await bot.call_with_reconnect(1, context, lambda store: "ok")
