import pytest

from recipebot.config import Config

BASE = {
    "TELEGRAM_TOKEN": "tok",
    "TELEGRAM_ALLOWED_USER_IDS": "12345,999",
    "TELEGRAM_OWNER_ID": "12345",
    "NOTION_CLIENT_ID": "client-id",
    "NOTION_CLIENT_SECRET": "client-secret",
    "NOTION_REDIRECT_URI": "https://recipebot.atiumaddict.com/oauth/callback",
}

OPTIONAL = ("LLM_PROVIDER", "LLM_MODEL_FAST", "LLM_MODEL_STRONG", "OAUTH_CALLBACK_PORT", "DB_PATH")
KEYS = ("GEMINI_API_KEY", "ANTHROPIC_API_KEY")


def set_env(monkeypatch, **overrides):
    for name in OPTIONAL + KEYS:
        monkeypatch.delenv(name, raising=False)
    for key, value in {**BASE, **overrides}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_from_env_reads_every_field(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key")

    cfg = Config.from_env()

    assert cfg.allowed_user_ids == frozenset({12345, 999})
    assert cfg.notion_client_id == "client-id"
    assert cfg.notion_client_secret == "client-secret"
    assert cfg.notion_redirect_uri == "https://recipebot.atiumaddict.com/oauth/callback"
    assert cfg.llm_api_key == "g-key"


def test_from_env_names_the_missing_variable(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key", TELEGRAM_TOKEN=None)

    with pytest.raises(RuntimeError, match="TELEGRAM_TOKEN"):
        Config.from_env()


def test_a_single_allowed_id_still_works(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key", TELEGRAM_ALLOWED_USER_IDS="12345")

    assert Config.from_env().allowed_user_ids == frozenset({12345})


def test_a_non_integer_id_fails_at_startup(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key", TELEGRAM_ALLOWED_USER_IDS="12345,abc")

    with pytest.raises(RuntimeError, match="abc"):
        Config.from_env()


def test_a_double_negative_id_fails_at_startup_instead_of_a_bare_valueerror(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key", TELEGRAM_ALLOWED_USER_IDS="5,--3")

    with pytest.raises(RuntimeError, match="--3"):
        Config.from_env()


def test_an_empty_allowlist_fails_at_startup(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key", TELEGRAM_ALLOWED_USER_IDS="")

    with pytest.raises(RuntimeError, match="TELEGRAM_ALLOWED_USER_IDS"):
        Config.from_env()


def test_the_oauth_callback_port_defaults(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key")

    assert Config.from_env().oauth_callback_port == 8080


def test_the_oauth_callback_port_is_overridable(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key", OAUTH_CALLBACK_PORT="9090")

    assert Config.from_env().oauth_callback_port == 9090


def test_the_db_path_defaults(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key")

    assert Config.from_env().db_path == "/data/recipebot.db"


def test_the_provider_defaults_to_gemini(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key")

    assert Config.from_env().llm_provider == "gemini"


def test_an_unknown_provider_fails_at_startup(monkeypatch):
    set_env(monkeypatch, LLM_PROVIDER="llamafile", GEMINI_API_KEY="g-key")

    with pytest.raises(RuntimeError) as excinfo:
        Config.from_env()

    message = str(excinfo.value)
    assert "llamafile" in message
    assert "gemini" in message
    assert "anthropic" in message


def test_only_the_selected_providers_key_is_required(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key")

    assert Config.from_env().llm_api_key == "g-key"

    set_env(monkeypatch, LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant")

    assert Config.from_env().llm_api_key == "sk-ant"


def test_the_model_ids_are_optional_overrides(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key")

    cfg = Config.from_env()
    assert (cfg.model_fast, cfg.model_strong) == ("", "")

    set_env(
        monkeypatch,
        GEMINI_API_KEY="g-key",
        LLM_MODEL_FAST="fast-id",
        LLM_MODEL_STRONG="strong-id",
    )

    cfg = Config.from_env()
    assert (cfg.model_fast, cfg.model_strong) == ("fast-id", "strong-id")


def test_no_secret_value_appears_in_the_config_repr(monkeypatch):
    set_env(
        monkeypatch,
        TELEGRAM_TOKEN="telegram-secret-value",
        NOTION_CLIENT_SECRET="notion-secret-value",
        GEMINI_API_KEY="gemini-secret-value",
    )

    text = repr(Config.from_env())

    assert "telegram-secret-value" not in text
    assert "notion-secret-value" not in text
    assert "gemini-secret-value" not in text
    assert "8080" in text


def test_the_owner_id_is_read(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key", TELEGRAM_OWNER_ID="12345")

    assert Config.from_env().owner_id == 12345


def test_a_missing_owner_id_fails_at_startup(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key", TELEGRAM_OWNER_ID=None)

    with pytest.raises(RuntimeError, match="TELEGRAM_OWNER_ID"):
        Config.from_env()


def test_a_non_integer_owner_id_fails_at_startup(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key", TELEGRAM_OWNER_ID="alex")

    with pytest.raises(RuntimeError, match="alex"):
        Config.from_env()


def test_an_owner_who_is_not_in_the_allowlist_fails_at_startup(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key", TELEGRAM_OWNER_ID="777")

    with pytest.raises(RuntimeError, match="TELEGRAM_OWNER_ID"):
        Config.from_env()


def test_a_superscript_owner_id_fails_with_a_clear_error(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key", TELEGRAM_OWNER_ID="\u00b2")

    with pytest.raises(RuntimeError, match="TELEGRAM_OWNER_ID"):
        Config.from_env()
