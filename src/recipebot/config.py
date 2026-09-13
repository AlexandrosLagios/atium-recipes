import os
from dataclasses import dataclass, field

DEFAULT_PROVIDER = "gemini"

PROVIDER_KEYS = {
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

_VARS = [
    "TELEGRAM_TOKEN",
    "TELEGRAM_ALLOWED_USER_ID",
    "NOTION_TOKEN",
    "NOTION_RECIPES_DS",
    "NOTION_INGREDIENTS_DS",
]


@dataclass(frozen=True)
class Config:
    telegram_token: str = field(repr=False)
    allowed_user_id: int
    notion_token: str = field(repr=False)
    recipes_ds: str
    ingredients_ds: str
    llm_provider: str
    llm_api_key: str = field(repr=False)
    model_fast: str = ""
    model_strong: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        provider = os.environ.get("LLM_PROVIDER") or DEFAULT_PROVIDER
        if provider not in PROVIDER_KEYS:
            accepted = ", ".join(sorted(PROVIDER_KEYS))
            raise RuntimeError(
                f"LLM_PROVIDER is {provider!r}; accepted values are {accepted}"
            )
        # Only the selected provider's key is required, so running on one
        # provider never means holding a key for the other.
        key_var = PROVIDER_KEYS[provider]
        missing = [name for name in _VARS + [key_var] if not os.environ.get(name)]
        if missing:
            raise RuntimeError(f"missing environment variables: {', '.join(missing)}")
        return cls(
            telegram_token=os.environ["TELEGRAM_TOKEN"],
            allowed_user_id=int(os.environ["TELEGRAM_ALLOWED_USER_ID"]),
            notion_token=os.environ["NOTION_TOKEN"],
            recipes_ds=os.environ["NOTION_RECIPES_DS"],
            ingredients_ds=os.environ["NOTION_INGREDIENTS_DS"],
            llm_provider=provider,
            llm_api_key=os.environ[key_var],
            model_fast=os.environ.get("LLM_MODEL_FAST", ""),
            model_strong=os.environ.get("LLM_MODEL_STRONG", ""),
        )
