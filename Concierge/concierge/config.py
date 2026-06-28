from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class Config:
    discord_bot_token: str
    finsearch_api_base: str
    finsearch_api_key: Optional[str]
    identity_db_path: str
    default_model: str = "gpt-4o-mini"
    request_timeout_s: float = 1260.0
    cooldown_s: float = 3.0
    edit_interval_s: float = 1.2
    edit_min_chars: int = 1500
    max_queue_per_user: int = 3


class ConfigError(ValueError):
    pass


def load_config(env: Mapping[str, str]) -> Config:
    token = (env.get("DISCORD_BOT_TOKEN") or "").strip()
    if not token:
        raise ConfigError("missing required env var: DISCORD_BOT_TOKEN")
    return Config(
        discord_bot_token=token,
        finsearch_api_base=(env.get("FINSEARCH_API_BASE") or "http://localhost:8000").rstrip("/"),
        finsearch_api_key=((env.get("FINGPT_API_KEY") or "").strip() or None),
        identity_db_path=(env.get("CONCIERGE_IDENTITY_DB") or "data/identity.sqlite"),
    )
