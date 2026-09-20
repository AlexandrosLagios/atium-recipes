import sqlite3
from dataclasses import dataclass

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_user_id INTEGER PRIMARY KEY,
    notion_access_token TEXT NOT NULL,
    notion_refresh_token TEXT,
    recipes_ds TEXT NOT NULL,
    ingredients_ds TEXT NOT NULL,
    workspace_name TEXT NOT NULL,
    connected_at INTEGER NOT NULL
)
"""

_ALLOWLIST_SCHEMA = """
CREATE TABLE IF NOT EXISTS allowed_users (
    telegram_user_id INTEGER PRIMARY KEY
)
"""

_COLUMNS = (
    "telegram_user_id",
    "notion_access_token",
    "notion_refresh_token",
    "recipes_ds",
    "ingredients_ds",
    "workspace_name",
    "connected_at",
)


@dataclass(frozen=True)
class UserRecord:
    telegram_user_id: int
    notion_access_token: str
    notion_refresh_token: str | None
    recipes_ds: str
    ingredients_ds: str
    workspace_name: str
    connected_at: int


class UserStore:
    """Two tables. `users` holds one row per connected Telegram user, and
    `allowed_users` holds the ids the owner allowed at runtime, which outlive a
    container rebuild that an env-var allowlist would not. Opens a fresh
    connection per call rather than holding one open, because handlers call in
    from asyncio.to_thread workers and a bot this size never needs a pool."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(_SCHEMA)
            conn.execute(_ALLOWLIST_SCHEMA)

    def get(self, telegram_user_id: int) -> UserRecord | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_user_id = ?", (telegram_user_id,)
            ).fetchone()
        return UserRecord(**{column: row[column] for column in _COLUMNS}) if row else None

    def save(self, record: UserRecord) -> None:
        placeholders = ", ".join("?" for _ in _COLUMNS)
        updates = ", ".join(f"{column} = excluded.{column}" for column in _COLUMNS[1:])
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"INSERT INTO users ({', '.join(_COLUMNS)}) VALUES ({placeholders}) "
                f"ON CONFLICT(telegram_user_id) DO UPDATE SET {updates}",
                tuple(getattr(record, column) for column in _COLUMNS),
            )

    def delete(self, telegram_user_id: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM users WHERE telegram_user_id = ?", (telegram_user_id,))

    def allowed_ids(self) -> frozenset[int]:
        """Read by the gate for a sender the environment does not already list.
        The table holds a handful of rows, so it stays a full read rather than
        a cache that could go stale."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT telegram_user_id FROM allowed_users").fetchall()
        return frozenset(row[0] for row in rows)

    def allow(self, telegram_user_id: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO allowed_users (telegram_user_id) VALUES (?)",
                (telegram_user_id,),
            )

    def deny(self, telegram_user_id: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM allowed_users WHERE telegram_user_id = ?", (telegram_user_id,)
            )
