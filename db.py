from __future__ import annotations

import json
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
                    catalog_ids TEXT NOT NULL DEFAULT '',
                    UNIQUE(user_id, domain, search_text, catalog_ids)
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
            # migrate: add catalog_ids column if missing
            cols = [r[1] for r in c.execute("PRAGMA table_info(user_watches)").fetchall()]
            if "catalog_ids" not in cols:
                c.execute("ALTER TABLE user_watches ADD COLUMN catalog_ids TEXT NOT NULL DEFAULT ''")

    # --- per-user watches ---

    @staticmethod
    def _encode_catalog_ids(catalog_ids: list[int] | None) -> str:
        if not catalog_ids:
            return ""
        return json.dumps(sorted(catalog_ids))

    @staticmethod
    def _decode_catalog_ids(raw: str) -> list[int]:
        if not raw:
            return []
        return json.loads(raw)

    def add_watch(self, user_id: int, domain: str, search_text: str,
                  catalog_ids: list[int] | None = None) -> int | None:
        """Add a watch. Returns the new watch id, or None if duplicate."""
        encoded = self._encode_catalog_ids(catalog_ids)
        try:
            with self._conn() as c:
                cur = c.execute(
                    "INSERT INTO user_watches (user_id, domain, search_text, catalog_ids) "
                    "VALUES (?, ?, ?, ?)",
                    (user_id, domain, search_text, encoded),
                )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def update_watch_catalogs(self, watch_id: int, catalog_ids: list[int] | None) -> None:
        encoded = self._encode_catalog_ids(catalog_ids)
        with self._conn() as c:
            c.execute(
                "UPDATE user_watches SET catalog_ids = ? WHERE id = ?",
                (encoded, watch_id),
            )

    def remove_watch_by_id(self, user_id: int, watch_id: int) -> bool:
        with self._conn() as c:
            return c.execute(
                "DELETE FROM user_watches WHERE id = ? AND user_id = ?",
                (watch_id, user_id),
            ).rowcount > 0

    def get_watches(self, user_id: int) -> list[tuple[int, str, str, list[int]]]:
        """Returns list of (id, domain, search_text, catalog_ids) for user."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, domain, search_text, catalog_ids FROM user_watches WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return [(wid, d, q, self._decode_catalog_ids(c)) for wid, d, q, c in rows]

    def get_all_watches(self) -> list[tuple[int, int, str, str, list[int]]]:
        """Returns all watches: (id, user_id, domain, search_text, catalog_ids)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, user_id, domain, search_text, catalog_ids FROM user_watches"
            ).fetchall()
        return [(wid, uid, d, q, self._decode_catalog_ids(c)) for wid, uid, d, q, c in rows]

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
