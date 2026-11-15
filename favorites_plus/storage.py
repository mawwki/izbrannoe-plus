"""Хранилище заметок «Избранное+»: SQLite + FTS5 (с LIKE-фолбэком)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'text',
    text        TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT 'other',
    confidence  REAL NOT NULL DEFAULT 0,
    source      TEXT NOT NULL DEFAULT 'local',
    tags        TEXT NOT NULL DEFAULT '[]',
    file_id     TEXT,
    url         TEXT,
    when_date   TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_notes_cat  ON notes(user_id, category);
CREATE TABLE IF NOT EXISTS corrections (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    text       TEXT NOT NULL,
    category   TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.fts_enabled = self._try_init_fts()
        self.conn.commit()

    def _try_init_fts(self) -> bool:
        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(text, content='notes', content_rowid='id')"
            )
            self.conn.execute(
                "CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN "
                "INSERT INTO notes_fts(rowid, text) VALUES (new.id, new.text); END"
            )
            self.conn.execute(
                "CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN "
                "INSERT INTO notes_fts(notes_fts, rowid, text) VALUES ('delete', old.id, old.text); END"
            )
            self.conn.execute(
                "CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE OF text ON notes BEGIN "
                "INSERT INTO notes_fts(notes_fts, rowid, text) VALUES ('delete', old.id, old.text); "
                "INSERT INTO notes_fts(rowid, text) VALUES (new.id, new.text); END"
            )
            self.conn.commit()
            self.conn.execute("SELECT * FROM notes_fts LIMIT 1")
            return True
        except sqlite3.OperationalError:
            self.conn.rollback()
            return False

    # ---------- заметки ----------

    def add_note(self, user_id: int, kind: str, text: str, category: str, confidence: float,
                 source: str, tags: Iterable[str] = (), file_id: str | None = None,
                 url: str | None = None, when_date: str | None = None,
                 created_at: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO notes (user_id, kind, text, category, confidence, source, tags, file_id, url, when_date, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, kind, text, category, float(confidence), source, json.dumps(list(tags), ensure_ascii=False),
             file_id, url, when_date, created_at or _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def find_same_text(self, user_id: int, text: str) -> dict[str, Any] | None:
        """Точный дубликат по тексту (для предупреждения при повторном сохранении)."""
        row = self.conn.execute(
            "SELECT * FROM notes WHERE user_id=? AND text=? ORDER BY id DESC LIMIT 1",
            (user_id, text),
        ).fetchone()
        return self._row_to_dict(row) if row is not None else None

    def notes_on_date(self, user_id: int, day: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM notes WHERE user_id=? AND when_date=? ORDER BY id DESC",
            (user_id, day),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_note(self, note_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        return self._row_to_dict(row) if row is not None else None

    def update_category(self, note_id: int, category: str, confidence: float = 1.0,
                        source: str = "user") -> None:
        self.conn.execute(
            "UPDATE notes SET category=?, confidence=?, source=? WHERE id=?",
            (category, confidence, source, note_id),
        )
        self.conn.commit()

    def delete_note(self, note_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def clear_user(self, user_id: int) -> int:
        cur = self.conn.execute("DELETE FROM notes WHERE user_id=?", (user_id,))
        self.conn.commit()
        return cur.rowcount

    def list_notes(self, user_id: int, category: str | None = None, limit: int = 10,
                   offset: int = 0) -> list[dict[str, Any]]:
        if category:
            rows = self.conn.execute(
                "SELECT * FROM notes WHERE user_id=? AND category=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (user_id, category, limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM notes WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count_by_category(self, user_id: int) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT category, COUNT(*) AS n FROM notes WHERE user_id=? GROUP BY category", (user_id,)
        ).fetchall()
        return {r["category"]: r["n"] for r in rows}

    def total(self, user_id: int) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS n FROM notes WHERE user_id=?", (user_id,)).fetchone()["n"])

    def all_texts(self, user_id: int) -> list[tuple[int, str]]:
        rows = self.conn.execute("SELECT id, text FROM notes WHERE user_id=? AND length(text)>0", (user_id,)).fetchall()
        return [(r["id"], r["text"]) for r in rows]

    def today_notes(self, user_id: int, since_local: str) -> list[dict[str, Any]]:
        # created_at хранится в локальном ISO — строковое сравнение корректно
        rows = self.conn.execute(
            "SELECT * FROM notes WHERE user_id=? AND substr(created_at,1,10)>=? ORDER BY id DESC",
            (user_id, since_local),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def dated_notes(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM notes WHERE user_id=? AND when_date IS NOT NULL ORDER BY when_date ASC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ---------- поиск ----------

    def fts_search(self, query: str, user_id: int, limit: int = 20) -> list[tuple[int, float]]:
        """Возвращает [(note_id, rank)], rank BM25 (меньше = лучше)."""
        tokens = [t for t in _fts_escape(query).split() if t]
        if not tokens:
            return []
        match = " OR ".join(tokens)
        try:
            rows = self.conn.execute(
                "SELECT notes_fts.rowid AS id, bm25(notes_fts) AS rank FROM notes_fts "
                "JOIN notes n ON n.id = notes_fts.rowid "
                "WHERE notes_fts MATCH ? AND n.user_id = ? ORDER BY rank LIMIT ?",
                (match, user_id, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return self._like_search(query, user_id, limit)
        return [(r["id"], r["rank"]) for r in rows]

    def _like_search(self, query: str, user_id: int, limit: int = 20) -> list[tuple[int, float]]:
        rows = self.conn.execute(
            "SELECT id FROM notes WHERE user_id=? AND text LIKE ? ORDER BY id DESC LIMIT ?",
            (user_id, f"%{query[:64]}%", limit),
        ).fetchall()
        return [(r["id"], 0.0) for r in rows]

    # ---------- правки пользователя (для дообучения) ----------

    def add_correction(self, user_id: int, text: str, category: str) -> None:
        self.conn.execute(
            "INSERT INTO corrections (user_id, text, category, created_at) VALUES (?,?,?,?)",
            (user_id, text, category, _now()),
        )
        self.conn.commit()

    def corrections(self, user_id: int | None = None) -> list[tuple[str, str]]:
        if user_id is None:
            rows = self.conn.execute("SELECT text, category FROM corrections").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT text, category FROM corrections WHERE user_id=?", (user_id,)
            ).fetchall()
        return [(r["text"], r["category"]) for r in rows]

    # ---------- служебное ----------

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except Exception:
            d["tags"] = []
        return d


def _fts_escape(query: str) -> str:
    """FTS5-синтаксис чреват ошибками: оставляем только буквы/цифры."""
    return "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in query).strip()
