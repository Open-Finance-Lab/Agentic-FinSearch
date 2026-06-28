import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS identity (
    discord_user_id   TEXT PRIMARY KEY,
    finsearch_user_id TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    atl_account_id    TEXT
);
"""


@dataclass(frozen=True)
class Identity:
    discord_user_id: str
    finsearch_user_id: str
    created_at: str
    atl_account_id: Optional[str]


class IdentityStore:
    def __init__(self, db_path: str) -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def get(self, discord_user_id: str) -> Optional[Identity]:
        row = self._conn.execute(
            "SELECT * FROM identity WHERE discord_user_id = ?", (discord_user_id,)
        ).fetchone()
        if row is None:
            return None
        return Identity(row["discord_user_id"], row["finsearch_user_id"],
                        row["created_at"], row["atl_account_id"])

    def resolve(self, discord_user_id: str, *, now_iso: str) -> Identity:
        existing = self.get(discord_user_id)
        if existing is not None:
            return existing
        finsearch_user_id = f"discord_{discord_user_id}"
        self._conn.execute(
            "INSERT INTO identity (discord_user_id, finsearch_user_id, created_at, atl_account_id) "
            "VALUES (?, ?, ?, NULL)",
            (discord_user_id, finsearch_user_id, now_iso),
        )
        self._conn.commit()
        return Identity(discord_user_id, finsearch_user_id, now_iso, None)

    def close(self) -> None:
        self._conn.close()
