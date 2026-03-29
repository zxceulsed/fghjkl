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
                CREATE TABLE IF NOT EXISTS watched (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    search_text TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS seen (
                    item_id INTEGER PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)

    # --- watched URLs ---

    def add_watch(self, search_text: str) -> bool:
        try:
            with self._conn() as c:
                c.execute("INSERT INTO watched (search_text) VALUES (?)", (search_text,))
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_watch(self, search_text: str) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM watched WHERE search_text = ?", (search_text,)).rowcount > 0

    def get_watches(self) -> list[str]:
        with self._conn() as c:
            return [r[0] for r in c.execute("SELECT search_text FROM watched").fetchall()]

    # --- seen items ---

    def is_seen(self, item_id: int) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM seen WHERE item_id = ?", (item_id,)).fetchone() is not None

    def mark_seen(self, item_ids: list[int]):
        with self._conn() as c:
            c.executemany("INSERT OR IGNORE INTO seen (item_id) VALUES (?)", [(i,) for i in item_ids])

    # --- settings ---

    def get_chat_id(self) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT value FROM settings WHERE key = 'chat_id'").fetchone()
            return row[0] if row else None

    def set_chat_id(self, chat_id: str):
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('chat_id', ?)", (chat_id,))
