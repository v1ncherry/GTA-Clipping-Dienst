"""SQLite-Speicher für Streamer, Clips, Accounts und Uploads."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS streamers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL,
    platform TEXT NOT NULL,
    channel TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    settings TEXT NOT NULL DEFAULT '{}',
    facecam TEXT,
    upload_targets TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    streamer_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at REAL NOT NULL,
    stream_title TEXT,
    category TEXT,
    score REAL,
    llm_score REAL,
    breakdown TEXT,
    chat_sample TEXT,
    dir TEXT,
    raw_path TEXT,
    raw_owned INTEGER NOT NULL DEFAULT 1,
    raw_start REAL,
    raw_end REAL,
    final_path TEXT,
    thumb_path TEXT,
    duration REAL,
    transcript TEXT,
    words TEXT,
    title TEXT,
    hook TEXT,
    caption TEXT,
    hashtags TEXT,
    layout TEXT,
    layout_override TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status);
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    name TEXT NOT NULL,
    credentials TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER NOT NULL,
    streamer_id INTEGER NOT NULL,
    verdict TEXT NOT NULL,
    reason TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    url TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""

JSON_COLUMNS = {"settings", "facecam", "upload_targets", "breakdown", "chat_sample", "words", "hashtags", "layout",
                "credentials", "render_opts"}

# Spalten, die nach der ersten Version dazugekommen sind (werden bei bestehenden Datenbanken nachgerüstet)
MIGRATIONS = {
    "clips": {"filter_reason": "TEXT", "forced": "INTEGER NOT NULL DEFAULT 0", "render_opts": "TEXT",
              "long_start": "REAL", "long_end": "REAL", "long_path": "TEXT", "long_duration": "REAL"},
}

CLIP_STATUSES = ("queued", "processing", "ready", "failed", "uploaded", "filtered", "discarded")


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            for table, cols in MIGRATIONS.items():
                have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
                for col, decl in cols.items():
                    if col not in have:
                        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    # ------------------------------------------------------------------ low level
    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        out = dict(row)
        for key in JSON_COLUMNS & out.keys():
            if out[key] is not None:
                try:
                    out[key] = json.loads(out[key])
                except (TypeError, ValueError):
                    pass
        return out

    @staticmethod
    def _encode(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return value

    def query(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(sql, args).fetchall()
        return [self._decode(r) for r in rows]  # type: ignore[misc]

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        with self.lock:
            row = self.conn.execute(sql, args).fetchone()
        return self._decode(row)

    def _insert(self, table: str, fields: dict) -> int:
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        with self.lock:
            cur = self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})",
                                    tuple(self._encode(v) for v in fields.values()))
            return int(cur.lastrowid)

    def _update(self, table: str, row_id: int, fields: dict) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.lock:
            self.conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?",
                              (*(self._encode(v) for v in fields.values()), row_id))

    # ------------------------------------------------------------------ streamers
    def add_streamer(self, name: str, url: str, platform: str, channel: str, settings: dict | None = None) -> int:
        return self._insert("streamers", {
            "name": name, "url": url, "platform": platform, "channel": channel,
            "settings": settings or {}, "created_at": time.time(),
        })

    def list_streamers(self, enabled_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM streamers" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY name COLLATE NOCASE"
        return self.query(sql)

    def get_streamer(self, sid: int) -> dict | None:
        return self.one("SELECT * FROM streamers WHERE id = ?", (sid,))

    def get_streamer_by_name(self, name: str) -> dict | None:
        return self.one("SELECT * FROM streamers WHERE name = ? COLLATE NOCASE", (name,))

    def update_streamer(self, sid: int, **fields) -> None:
        self._update("streamers", sid, fields)

    def delete_streamer(self, sid: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM streamers WHERE id = ?", (sid,))

    # ------------------------------------------------------------------ clips
    def add_clip(self, **fields) -> int:
        fields.setdefault("created_at", time.time())
        fields.setdefault("status", "queued")
        return self._insert("clips", fields)

    def update_clip(self, cid: int, **fields) -> None:
        self._update("clips", cid, fields)

    def get_clip(self, cid: int) -> dict | None:
        return self.one("SELECT * FROM clips WHERE id = ?", (cid,))

    def list_clips(self, status: str | None = None, streamer_id: int | None = None, limit: int = 100) -> list[dict]:
        where, args = [], []
        if status == "open":
            where.append("status IN ('queued','processing','ready','failed')")
        elif status == "working":
            where.append("status IN ('queued','processing','failed')")
        elif status and status != "all":
            where.append("status = ?")
            args.append(status)
        else:
            where.append("status != 'discarded'")
        if streamer_id:
            where.append("streamer_id = ?")
            args.append(streamer_id)
        sql = "SELECT * FROM clips WHERE " + " AND ".join(where) + " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        return self.query(sql, tuple(args))

    def count_clips_by_status(self, streamer_id: int | None = None) -> dict[str, int]:
        sql = "SELECT status, COUNT(*) AS n FROM clips" + (" WHERE streamer_id = ?" if streamer_id else "")
        rows = self.query(sql + " GROUP BY status", (streamer_id,) if streamer_id else ())
        return {r["status"]: int(r["n"]) for r in rows}

    def count_clips_since(self, streamer_id: int, since: float) -> int:
        row = self.one("SELECT COUNT(*) AS n FROM clips WHERE streamer_id = ? AND created_at >= ? "
                       "AND status != 'discarded'", (streamer_id, since))
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------------ feedback
    def add_feedback(self, clip_id: int, streamer_id: int, verdict: str, reason: str | None = None) -> int:
        return self._insert("feedback", {"clip_id": clip_id, "streamer_id": streamer_id, "verdict": verdict,
                                         "reason": reason, "created_at": time.time()})

    # ------------------------------------------------------------------ settings
    def get_setting(self, key: str, default=None):
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            return default

    def set_setting(self, key: str, value) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                              "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                              (key, json.dumps(value, ensure_ascii=False)))

    # ------------------------------------------------------------------ accounts
    def add_account(self, platform: str, name: str, credentials: dict) -> int:
        return self._insert("accounts", {"platform": platform, "name": name, "credentials": credentials,
                                         "created_at": time.time()})

    def list_accounts(self) -> list[dict]:
        return self.query("SELECT * FROM accounts ORDER BY platform, name")

    def get_account(self, aid: int) -> dict | None:
        return self.one("SELECT * FROM accounts WHERE id = ?", (aid,))

    def update_account(self, aid: int, **fields) -> None:
        self._update("accounts", aid, fields)

    def delete_account(self, aid: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM accounts WHERE id = ?", (aid,))

    # ------------------------------------------------------------------ uploads
    def add_upload(self, clip_id: int, account_id: int) -> int:
        now = time.time()
        return self._insert("uploads", {"clip_id": clip_id, "account_id": account_id, "status": "pending",
                                        "created_at": now, "updated_at": now})

    def update_upload(self, uid: int, **fields) -> None:
        fields["updated_at"] = time.time()
        self._update("uploads", uid, fields)

    def list_uploads(self, clip_id: int) -> list[dict]:
        return self.query("SELECT u.*, a.platform, a.name AS account_name FROM uploads u "
                          "LEFT JOIN accounts a ON a.id = u.account_id WHERE u.clip_id = ? ORDER BY u.id", (clip_id,))
