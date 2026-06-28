from dataclasses import dataclass

_PREFIX = "discord"


def make_session_id(discord_user_id: str, location_id: str) -> str:
    if not discord_user_id or not location_id:
        raise ValueError("discord_user_id and location_id are required")
    if ":" in discord_user_id or ":" in location_id:
        raise ValueError("ids must not contain ':'")
    return f"{_PREFIX}:{discord_user_id}:{location_id}"


@dataclass(frozen=True)
class SessionRef:
    discord_user_id: str
    location_id: str


def parse_session_id(session_id: str) -> SessionRef:
    parts = session_id.split(":")
    if len(parts) != 3 or parts[0] != _PREFIX or not parts[1] or not parts[2]:
        raise ValueError(f"malformed session_id: {session_id!r}")
    return SessionRef(discord_user_id=parts[1], location_id=parts[2])
