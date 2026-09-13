import base64
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize"
TOKEN_URL = "https://api.notion.com/v1/oauth/token"
TIMEOUT_S = 10


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str | None
    workspace_name: str


def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "owner": "user",
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def _basic_auth(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _token_pair(client_id: str, client_secret: str, payload: dict, fallback_refresh: str | None) -> TokenPair:
    response = httpx.post(
        TOKEN_URL,
        headers={"Authorization": _basic_auth(client_id, client_secret)},
        json=payload,
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    body = response.json()
    return TokenPair(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token", fallback_refresh),
        workspace_name=body.get("workspace_name", ""),
    )


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str) -> TokenPair:
    return _token_pair(
        client_id,
        client_secret,
        {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        fallback_refresh=None,
    )


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> TokenPair:
    return _token_pair(
        client_id,
        client_secret,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
        fallback_refresh=refresh_token,
    )


def _page_title(page: dict) -> str:
    spans = page.get("properties", {}).get("title", {}).get("title", [])
    return "".join(span["plain_text"] for span in spans).strip()


_PAGE_PARENT_TYPES = {"workspace", "page_id"}


def find_shared_page(client) -> tuple[str, str] | None:
    # Notion does not guarantee a stable order, so this always takes the
    # first result; the caller tells the user which page it picked. A search
    # with no query also returns every page the integration can see,
    # including recipe pages nested under this user's own databases after a
    # reconnect, so only a page whose parent is the workspace or another page
    # (never a database row) can be the shared root.
    result = client.search(filter={"property": "object", "value": "page"})
    pages = [
        page
        for page in result.get("results", [])
        if page.get("parent", {}).get("type") in _PAGE_PARENT_TYPES
    ]
    if not pages:
        return None
    page = pages[0]
    return page["id"], _page_title(page)
