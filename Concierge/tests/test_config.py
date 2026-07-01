import pytest
from concierge.config import load_config, Config, ConfigError


def test_load_full_env():
    cfg = load_config({"DISCORD_BOT_TOKEN": "tok",
                       "FINSEARCH_API_BASE": "http://localhost:8000/"})
    assert isinstance(cfg, Config)
    assert cfg.discord_bot_token == "tok"
    assert cfg.finsearch_api_base == "http://localhost:8000"   # trailing slash stripped
    assert cfg.finsearch_api_key is None                       # absent -> None
    assert cfg.default_model == "gpt-4o-mini"


def test_missing_token_raises():
    with pytest.raises(ConfigError):
        load_config({"FINSEARCH_API_BASE": "http://x"})


def test_default_base_when_absent():
    cfg = load_config({"DISCORD_BOT_TOKEN": "tok"})
    assert cfg.finsearch_api_base == "http://localhost:8000"
