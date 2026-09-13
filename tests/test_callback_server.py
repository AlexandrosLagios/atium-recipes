import time
from http.client import HTTPConnection

import pytest

from recipebot import callback_server, oauth
from recipebot.config import Config
from recipebot.users import UserStore

FIXTURE = {
    "recipes": {"properties": {"Name": {"type": "title", "title": {}}}},
    "ingredients": {
        "properties": {
            "Name": {"type": "title", "title": {}},
            "Used in": {"type": "rollup", "rollup": {"relation_property_name": "Recipes", "rollup_property_name": "Name", "function": "count"}},
        }
    },
}


def a_config(**overrides) -> Config:
    fields = dict(
        telegram_token="tok",
        allowed_user_ids=frozenset({1}),
        notion_client_id="client-1",
        notion_client_secret="secret-1",
        notion_redirect_uri="http://127.0.0.1:0/oauth/callback",
        oauth_callback_port=0,
        db_path=":memory:",
        llm_provider="gemini",
        llm_api_key="key",
    )
    return Config(**{**fields, **overrides})


def test_start_connect_returns_an_authorize_url_and_remembers_the_state():
    callback_server.PENDING.clear()
    cfg = a_config()

    url = callback_server.start_connect(42, cfg)

    assert url.startswith(oauth.AUTHORIZE_URL)
    assert len(callback_server.PENDING) == 1
    state = next(iter(callback_server.PENDING))
    assert callback_server.PENDING[state] == 42


class FakeSearch:
    def __call__(self, **kwargs):
        return {
            "results": [
                {"id": "page-1", "properties": {"title": {"title": [{"plain_text": "Kitchen"}]}}}
            ]
        }


class FakeChildrenList:
    def list(self, block_id, **kwargs):
        return {"results": [], "has_more": False, "next_cursor": None}


class FakeDatabases:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return {"id": f"db{len(self.created)}", "data_sources": [{"id": f"ds{len(self.created)}"}]}


class FakeDataSources:
    def retrieve(self, data_source_id):
        return {"properties": {"Recipes": {"type": "relation", "relation": {"data_source_id": "ds2"}}}}

    def update(self, **kwargs):
        pass


class FakeNotionClient:
    def __init__(self, *args, **kwargs):
        self.search = FakeSearch()
        self.blocks = type("B", (), {"children": FakeChildrenList()})()
        self.databases = FakeDatabases()
        self.data_sources = FakeDataSources()


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(callback_server, "Client", FakeNotionClient)
    monkeypatch.setattr(
        oauth,
        "exchange_code",
        lambda *a, **k: oauth.TokenPair(access_token="tok-1", refresh_token="refresh-1", workspace_name="Alex's Kitchen"),
    )
    callback_server.PENDING.clear()
    cfg = a_config()
    users = UserStore(str(tmp_path / "users.db"))
    notifications = []
    http_server = callback_server.make_server(
        cfg, users, FIXTURE, lambda chat_id, text: notifications.append((chat_id, text))
    )
    thread = callback_server.run_in_background(http_server)
    yield http_server, users, notifications
    http_server.shutdown()
    thread.join(timeout=2)


def _get(server, path):
    port = server.server_address[1]
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response.status, body


def test_a_valid_callback_connects_the_user(server):
    http_server, users, notifications = server
    callback_server.PENDING["state-1"] = 42

    status, body = _get(http_server, "/oauth/callback?code=abc&state=state-1")

    assert status == 200
    record = users.get(42)
    assert record.notion_access_token == "tok-1"
    assert record.workspace_name == "Alex's Kitchen"
    assert notifications == [(42, "Connected to 'Kitchen'. Send me a recipe.")]
    assert "state-1" not in callback_server.PENDING


def test_an_unknown_state_connects_nobody(server):
    http_server, users, notifications = server

    status, body = _get(http_server, "/oauth/callback?code=abc&state=does-not-exist")

    assert status == 200
    assert b"expired" in body.lower()
    assert notifications == []
