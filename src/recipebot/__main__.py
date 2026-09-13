import asyncio
import logging

from telegram import Update
from telegram.ext import Application

from .bot import build_application
from .callback_server import make_server, run_in_background
from .config import Config
from .llm import Extractor
from .notion import load_schema_fixture
from .users import UserStore


def _build_post_init(cfg, users, fixture):
    async def post_init(application: Application) -> None:
        loop = asyncio.get_running_loop()

        def notify(chat_id: int, text: str) -> None:
            asyncio.run_coroutine_threadsafe(application.bot.send_message(chat_id, text), loop)

        server = make_server(cfg, users, fixture, notify)
        run_in_background(server)

    return post_init


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cfg = Config.from_env()
    users = UserStore(cfg.db_path)
    fixture = load_schema_fixture()

    application = build_application(
        cfg, users, Extractor.from_config(cfg), post_init=_build_post_init(cfg, users, fixture)
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
