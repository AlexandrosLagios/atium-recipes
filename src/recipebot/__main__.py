import logging

from telegram import Update

from .bot import build_application
from .config import Config
from .llm import Extractor
from .notion import NotionStore


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cfg = Config.from_env()
    application = build_application(
        cfg, NotionStore.from_config(cfg), Extractor.from_config(cfg)
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
