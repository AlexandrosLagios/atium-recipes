import httpx
import pytest

from recipebot.oauth import (
    TokenPair,
    build_authorize_url,
    exchange_code,
    find_shared_page,
    refresh_access_token,
)


def test_the_authorize_url_carries_the_state_and_redirect(monkeypatch):
    url = build_authorize_url("client-1", "https://bot.example/oauth/callback", "state-1")

    assert url.startswith("https://api.notion.com/v1/oauth/authorize?")
    assert "client_id=client-1" in url
    assert "state=state-1" in url
    assert "redirect_uri=https%3A%2F%2Fbot.example%2Foauth%2Fcallback" in url


def _mock_token_post(monkeypatch, body, status_code=200):
    def fake_post(url, headers, json, timeout):
        request = httpx.Request("POST", url)
        return httpx.Response(status_code, json=body, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)


def test_exchange_code_reads_the_token_pair(monkeypatch):
    _mock_token_post(
        monkeypatch,
        {"access_token": "tok-1", "refresh_token": "refresh-1", "workspace_name": "Alex's Kitchen"},
    )

    tokens = exchange_code("client-1", "secret-1", "https://bot.example/oauth/callback", "code-1")

    assert tokens == TokenPair(access_token="tok-1", refresh_token="refresh-1", workspace_name="Alex's Kitchen")


def test_exchange_code_raises_on_a_failed_exchange(monkeypatch):
    _mock_token_post(monkeypatch, {"error": "invalid_grant"}, status_code=400)

    with pytest.raises(httpx.HTTPStatusError):
        exchange_code("client-1", "secret-1", "https://bot.example/oauth/callback", "bad-code")


def test_refresh_access_token_reads_the_new_pair(monkeypatch):
    _mock_token_post(
        monkeypatch,
        {"access_token": "tok-2", "refresh_token": "refresh-2", "workspace_name": "Alex's Kitchen"},
    )

    tokens = refresh_access_token("client-1", "secret-1", "refresh-1")

    assert tokens.access_token == "tok-2"
    assert tokens.refresh_token == "refresh-2"


def test_refresh_access_token_keeps_the_old_refresh_token_when_none_is_returned(monkeypatch):
    _mock_token_post(monkeypatch, {"access_token": "tok-2", "workspace_name": "Alex's Kitchen"})

    tokens = refresh_access_token("client-1", "secret-1", "refresh-1")

    assert tokens.refresh_token == "refresh-1"


class FakeSearch:
    def __init__(self, pages):
        self.pages = pages

    def __call__(self, **kwargs):
        assert kwargs["filter"] == {"property": "object", "value": "page"}
        return {"results": self.pages}


def _page(page_id, title):
    return {
        "id": page_id,
        "properties": {"title": {"title": [{"plain_text": title}]}},
    }


def test_find_shared_page_returns_the_first_page():
    client = type("C", (), {"search": FakeSearch([_page("p1", "Kitchen"), _page("p2", "Other")])})()

    assert find_shared_page(client) == ("p1", "Kitchen")


def test_find_shared_page_returns_none_when_nothing_was_shared():
    client = type("C", (), {"search": FakeSearch([])})()

    assert find_shared_page(client) is None
