import os
from dataclasses import dataclass

_VARS = [
    "TELEGRAM_TOKEN",
    "TELEGRAM_ALLOWED_USER_ID",
    "NOTION_TOKEN",
    "NOTION_RECIPES_DS",
    "NOTION_INGREDIENTS_DS",
    "ANTHROPIC_API_KEY",
]


@dataclass(frozen=True)
class Config:
    telegram_token: str
    allowed_user_id: int
    notion_token: str
    recipes_ds: str
    ingredients_ds: str
    anthropic_key: str

    @classmethod
    def from_env(cls) -> "Config":
        missing = [name for name in _VARS if not os.environ.get(name)]
        if missing:
            raise RuntimeError(f"missing environment variables: {', '.join(missing)}")
        return cls(
            telegram_token=os.environ["TELEGRAM_TOKEN"],
            allowed_user_id=int(os.environ["TELEGRAM_ALLOWED_USER_ID"]),
            notion_token=os.environ["NOTION_TOKEN"],
            recipes_ds=os.environ["NOTION_RECIPES_DS"],
            ingredients_ds=os.environ["NOTION_INGREDIENTS_DS"],
            anthropic_key=os.environ["ANTHROPIC_API_KEY"],
        )
