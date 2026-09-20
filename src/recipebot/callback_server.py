import logging
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from notion_client import Client

from . import oauth
from .config import Config
from .notion import create_user_databases
from .strings import DEFAULT as DEFAULT_LANGUAGE, language_markup, t
from .users import UserRecord

log = logging.getLogger(__name__)

# ponytail: an in-progress connect attempt is a handful of seconds of state,
# same lifetime philosophy as bot.PREVIEWS. A restart loses it and the user
# taps Connect again. Carries the language the user was reading when they
# tapped Connect, because this thread never sees their Telegram update.
PENDING: dict[str, tuple[int, str]] = {}


def start_connect(telegram_user_id: int, cfg: Config, language: str = DEFAULT_LANGUAGE) -> str:
    state = uuid.uuid4().hex
    PENDING[state] = (telegram_user_id, language)
    return oauth.build_authorize_url(cfg.notion_client_id, cfg.notion_redirect_uri, state)


def _handle_connect(cfg, users, fixture, notify, chat_id: int, code: str, language: str) -> None:
    tokens = oauth.exchange_code(cfg.notion_client_id, cfg.notion_client_secret, cfg.notion_redirect_uri, code)
    client = Client(auth=tokens.access_token)
    page = oauth.find_shared_page(client)
    if page is None:
        notify(chat_id, t(language, "no_shared_page"))
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
            language=language,
        )
    )
    # The language buttons ride along with the confirmation rather than waiting
    # for the next message, so the choice is the first thing a new user makes.
    notify(chat_id, t(language, "connected", page=page_title), language_markup())


def _page(message: str) -> bytes:
    return f"<p>{message}</p>".encode()


def make_server(cfg, users, fixture: dict, notify) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = parse_qs(urlsplit(self.path).query)
            state = query.get("state", [""])[0]
            code = query.get("code", [""])[0]
            pending = PENDING.pop(state, None)
            if pending is None:
                # Nobody's language is known for a state this server never
                # issued, so this one page stays in the default language.
                self._respond(200, t(DEFAULT_LANGUAGE, "page_expired"))
                return
            chat_id, language = pending
            try:
                _handle_connect(cfg, users, fixture, notify, chat_id, code, language)
            except Exception:
                log.exception("oauth callback failed for chat %s", chat_id)
                notify(chat_id, t(language, "connect_failed"))
                self._respond(200, t(language, "page_failed"))
                return
            self._respond(200, t(language, "page_connected"))

        def _respond(self, status: int, message: str) -> None:
            self.send_response(status)
            # _page encodes as UTF-8, and a Greek page renders as mojibake
            # without the charset, because HTML has no meta tag to fall back on.
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_page(message))

        def log_message(self, format, *args):
            pass  # ponytail: BaseHTTPRequestHandler logs every request to stderr; the app's own logger already covers this.

    return ThreadingHTTPServer(("0.0.0.0", cfg.oauth_callback_port), Handler)


def run_in_background(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread
