import pytest

from recipebot.config import Config

BASE = {
    "TELEGRAM_TOKEN": "tok",
    "TELEGRAM_ALLOWED_USER_ID": "12345",
    "NOTION_TOKEN": "ntn",
    "NOTION_RECIPES_DS": "ds-recipes",
    "NOTION_INGREDIENTS_DS": "ds-ingredients",
}

OPTIONAL = ("LLM_PROVIDER", "LLM_MODEL_FAST", "LLM_MODEL_STRONG")
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

    assert cfg.allowed_user_id == 12345
    assert cfg.recipes_ds == "ds-recipes"
    assert cfg.notion_token == "ntn"
    assert cfg.llm_api_key == "g-key"


def test_from_env_names_the_missing_variable(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key", TELEGRAM_TOKEN=None)

    with pytest.raises(RuntimeError, match="TELEGRAM_TOKEN"):
        Config.from_env()


def test_the_provider_defaults_to_gemini(monkeypatch):
    set_env(monkeypatch, GEMINI_API_KEY="g-key")

    assert Config.from_env().llm_provider == "gemini"


def test_an_empty_provider_falls_back_to_the_default(monkeypatch):
    set_env(monkeypatch, LLM_PROVIDER="", GEMINI_API_KEY="g-key")

    assert Config.from_env().llm_provider == "gemini"


def test_anthropic_is_selectable(monkeypatch):
    set_env(monkeypatch, LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant")

    cfg = Config.from_env()

    assert cfg.llm_provider == "anthropic"
    assert cfg.llm_api_key == "sk-ant"


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


def test_the_missing_key_named_is_the_selected_providers_key(monkeypatch):
    set_env(monkeypatch, LLM_PROVIDER="anthropic")

    with pytest.raises(RuntimeError) as excinfo:
        Config.from_env()

    assert "ANTHROPIC_API_KEY" in str(excinfo.value)
    assert "GEMINI_API_KEY" not in str(excinfo.value)


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
