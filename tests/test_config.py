import pytest
from recipebot.config import Config


def test_from_env_reads_every_field(monkeypatch):
    for key, value in {
        "TELEGRAM_TOKEN": "tok",
        "TELEGRAM_ALLOWED_USER_ID": "12345",
        "NOTION_TOKEN": "ntn",
        "NOTION_RECIPES_DS": "ds-recipes",
        "NOTION_INGREDIENTS_DS": "ds-ingredients",
        "ANTHROPIC_API_KEY": "sk-ant",
    }.items():
        monkeypatch.setenv(key, value)

    cfg = Config.from_env()

    assert cfg.allowed_user_id == 12345
    assert cfg.recipes_ds == "ds-recipes"


def test_from_env_names_the_missing_variable(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "1")
    monkeypatch.setenv("NOTION_TOKEN", "n")
    monkeypatch.setenv("NOTION_RECIPES_DS", "r")
    monkeypatch.setenv("NOTION_INGREDIENTS_DS", "i")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")

    with pytest.raises(RuntimeError, match="TELEGRAM_TOKEN"):
        Config.from_env()
