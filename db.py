from __future__ import annotations

import sqlite3


class Database:
    def __init__(self, path: str = "bot.db"):
        self.path = path
        self._init()

    def _conn(self):
        return sqlite3.connect(self.path)

    def _init(self):
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS user_watches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    domain TEXT NOT NULL,
                    search_text TEXT NOT NULL,
                    UNIQUE(user_id, domain, search_text)
                );
                CREATE TABLE IF NOT EXISTS seen (
                    item_id INTEGER PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    chat_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS allowed_users (
                    user_id INTEGER PRIMARY KEY
                );
            """)

    # --- per-user watches ---

    def add_watch(self, user_id: int, domain: str, search_text: str) -> bool:
        try:
            with self._conn() as c:
                c.execute(
                    "INSERT INTO user_watches (user_id, domain, search_text) VALUES (?, ?, ?)",
                    (user_id, domain, search_text),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_watch_by_id(self, user_id: int, watch_id: int) -> bool:
        with self._conn() as c:
            return c.execute(
                "DELETE FROM user_watches WHERE id = ? AND user_id = ?",
                (watch_id, user_id),
            ).rowcount > 0

    def get_watches(self, user_id: int) -> list[tuple[int, str, str]]:
        """Returns list of (id, domain, search_text) for user."""
        with self._conn() as c:
            return c.execute(
                "SELECT id, domain, search_text FROM user_watches WHERE user_id = ?",
                (user_id,),
            ).fetchall()

    def get_all_watches(self) -> list[tuple[int, int, str, str]]:
        """Returns all watches: (id, user_id, domain, search_text)."""
        with self._conn() as c:
            return c.execute(
                "SELECT id, user_id, domain, search_text FROM user_watches"
            ).fetchall()

    # --- seen items ---

    def is_seen(self, item_id: int) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM seen WHERE item_id = ?", (item_id,)).fetchone() is not None

    def mark_seen(self, item_ids: list[int]):
        with self._conn() as c:
            c.executemany("INSERT OR IGNORE INTO seen (item_id) VALUES (?)", [(i,) for i in item_ids])

    # --- per-user chat_id ---

    def get_chat_id(self, user_id: int) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT chat_id FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
            return row[0] if row else None

    def set_chat_id(self, user_id: int, chat_id: str):
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO user_settings (user_id, chat_id) VALUES (?, ?)",
                (user_id, chat_id),
            )

    # --- allowed users (whitelist) ---

    def add_allowed_user(self, user_id: int) -> bool:
        try:
            with self._conn() as c:
                c.execute("INSERT INTO allowed_users (user_id) VALUES (?)", (user_id,))
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_allowed_user(self, user_id: int) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM allowed_users WHERE user_id = ?", (user_id,)).rowcount > 0

    def is_allowed(self, user_id: int) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM allowed_users WHERE user_id = ?", (user_id,)).fetchone() is not None

    def get_allowed_users(self) -> list[int]:
        with self._conn() as c:
            return [r[0] for r in c.execute("SELECT user_id FROM allowed_users").fetchall()]
