import logging
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

from notion_client import Client

from . import oauth
from .notion import create_user_databases
from .users import UserRecord

log = logging.getLogger(__name__)

# ponytail: an in-progress connect attempt is a handful of seconds of state,
# same lifetime philosophy as bot.PREVIEWS. A restart loses it and the user
# taps Connect again.
PENDING: dict[str, int] = {}


def start_connect(telegram_user_id: int, cfg) -> str:
    state = uuid.uuid4().hex
    PENDING[state] = telegram_user_id
    return oauth.build_authorize_url(cfg.notion_client_id, cfg.notion_redirect_uri, state)


def _handle_connect(cfg, users, fixture, notify, chat_id: int, code: str) -> None:
    tokens = oauth.exchange_code(cfg.notion_client_id, cfg.notion_client_secret, cfg.notion_redirect_uri, code)
    client = Client(auth=tokens.access_token)
    page = oauth.find_shared_page(client)
    if page is None:
        notify((chat_id, "I didn't see a shared page. Send me a message and try again, and share a page this time."))
        return
    page_id, page_title = page
    recipes_ds, ingredients_ds = create_user_databases(client, page_id, fixture)
    users.save(
        UserRecord(
            telegram_user_id=chat_id,
            notion_access_token=tokens.access_token,
            notion_refresh_token=tokens.refresh_token,
            recipes_ds=recipes_ds,
            ingredients_ds=ingredients_ds,
            workspace_name=tokens.workspace_name,
            connected_at=int(time.time()),
        )
    )
    notify((chat_id, f"Connected to '{page_title}'. Send me a recipe."))


def _page(message: str) -> bytes:
    return f"<p>{message}</p>".encode()


def make_server(cfg, users, fixture: dict, notify) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = parse_qs(urlsplit(self.path).query)
            state = query.get("state", [""])[0]
            code = query.get("code", [""])[0]
            chat_id = PENDING.pop(state, None)
            if chat_id is None:
                self._respond(200, "This link expired. Open Telegram and send a message to connect again.")
                return
            try:
                _handle_connect(cfg, users, fixture, notify, chat_id, code)
            except Exception:
                log.exception("oauth callback failed for chat %s", chat_id)
                notify((chat_id, "Connecting to Notion failed. Send me a message to try again."))
                self._respond(200, "Something went wrong. Check Telegram for what to do next.")
                return
            self._respond(200, "Connected. Go back to Telegram.")

        def _respond(self, status: int, message: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(_page(message))

        def log_message(self, format, *args):
            pass  # ponytail: BaseHTTPRequestHandler logs every request to stderr; the app's own logger already covers this.

    return HTTPServer(("127.0.0.1", cfg.oauth_callback_port), Handler)


def run_in_background(server: HTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread
