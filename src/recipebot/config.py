import os
from dataclasses import dataclass, field

DEFAULT_PROVIDER = "gemini"
DEFAULT_OAUTH_CALLBACK_PORT = 8080
DEFAULT_DB_PATH = "/data/recipebot.db"

PROVIDER_KEYS = {
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

_VARS = [
    "TELEGRAM_TOKEN",
    "TELEGRAM_ALLOWED_USER_IDS",
    "TELEGRAM_OWNER_ID",
    "NOTION_CLIENT_ID",
    "NOTION_CLIENT_SECRET",
    "NOTION_REDIRECT_URI",
]


def _parse_allowed_ids(raw: str) -> frozenset[int]:
    ids = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.lstrip("-").isdecimal() or part.count("-") > 1:
            raise RuntimeError(f"TELEGRAM_ALLOWED_USER_IDS contains a non-integer id: {part!r}")
        ids.append(int(part))
    if not ids:
        raise RuntimeError("TELEGRAM_ALLOWED_USER_IDS must list at least one Telegram user id")
    return frozenset(ids)


def _parse_owner_id(raw: str) -> int:
    if not raw.strip().isdecimal():
        raise RuntimeError(f"TELEGRAM_OWNER_ID is not a Telegram user id: {raw!r}")
    return int(raw)


@dataclass(frozen=True)
class Config:
    telegram_token: str = field(repr=False)
    allowed_user_ids: frozenset[int]
    notion_client_id: str = field(repr=False)
    notion_client_secret: str = field(repr=False)
    notion_redirect_uri: str
    oauth_callback_port: int
    db_path: str
    llm_provider: str
    llm_api_key: str = field(repr=False)
    owner_id: int = 0
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
        owner_id = _parse_owner_id(os.environ["TELEGRAM_OWNER_ID"])
        # The owner joins the allowlist here rather than being required to
        # appear in both variables, so editing one can never lock them out.
        allowed_user_ids = _parse_allowed_ids(os.environ["TELEGRAM_ALLOWED_USER_IDS"]) | {owner_id}
        return cls(
            telegram_token=os.environ["TELEGRAM_TOKEN"],
            allowed_user_ids=allowed_user_ids,
            notion_client_id=os.environ["NOTION_CLIENT_ID"],
            notion_client_secret=os.environ["NOTION_CLIENT_SECRET"],
            notion_redirect_uri=os.environ["NOTION_REDIRECT_URI"],
            oauth_callback_port=int(os.environ.get("OAUTH_CALLBACK_PORT", DEFAULT_OAUTH_CALLBACK_PORT)),
            db_path=os.environ.get("DB_PATH", DEFAULT_DB_PATH),
            llm_provider=provider,
            llm_api_key=os.environ[key_var],
            owner_id=owner_id,
            model_fast=os.environ.get("LLM_MODEL_FAST", ""),
            model_strong=os.environ.get("LLM_MODEL_STRONG", ""),
        )
