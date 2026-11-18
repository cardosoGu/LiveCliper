"""Persistência em SQLite: candidatos, publicações, blocklist e um kv simples."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .models import Candidate, State
from .timeutil import parse_iso, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL, source TEXT NOT NULL, clip_id TEXT NOT NULL UNIQUE,
  clip_url TEXT NOT NULL, channel TEXT NOT NULL, created_at TEXT NOT NULL,
  duration REAL NOT NULL, title TEXT NOT NULL, viewers INTEGER NOT NULL,
  view_count INTEGER NOT NULL, spike_score REAL NOT NULL, rank REAL NOT NULL,
  state TEXT NOT NULL, raw_path TEXT, vertical_path TEXT, transcript TEXT,
  ai_score REAL, ai_json TEXT, reason TEXT, updated_at TEXT NOT NULL,
  extra_clip_url TEXT, approval_message_id INTEGER, priority INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cand_state ON candidates(state);
CREATE INDEX IF NOT EXISTS idx_cand_channel ON candidates(channel, created_at);
CREATE TABLE IF NOT EXISTS publications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL, platform TEXT NOT NULL,
  post_id TEXT, url TEXT, published_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blocklist (
  channel TEXT PRIMARY KEY, reason TEXT, added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_COLS = [
    "platform", "source", "clip_id", "clip_url", "channel", "created_at",
    "duration", "title", "viewers", "view_count", "spike_score", "rank",
    "state", "raw_path", "vertical_path", "transcript", "ai_score", "ai_json",
    "reason", "updated_at", "extra_clip_url", "approval_message_id", "priority",
]


def _to_row(c: Candidate) -> list:
    values = []
    for col in _COLS:
        v = getattr(c, col)
        if isinstance(v, datetime):
            v = v.isoformat()
        elif isinstance(v, State):
            v = v.value
        values.append(v)
    return values


def _from_row(row: sqlite3.Row) -> Candidate:
    d = dict(row)
    d["created_at"] = parse_iso(d["created_at"])
    d["updated_at"] = parse_iso(d["updated_at"])
    d["state"] = State(d["state"])
    return Candidate(**d)


class DB:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    # colunas que bancos antigos podem não ter: nome -> declaração
    _NEW_COLUMNS = {
        "extra_clip_url": "TEXT",
        "approval_message_id": "INTEGER",
        "priority": "INTEGER NOT NULL DEFAULT 0",
    }

    def _migrate(self) -> None:
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(candidates)")}
        for name, decl in self._NEW_COLUMNS.items():
            if name not in cols:
                self.conn.execute(f"ALTER TABLE candidates ADD COLUMN {name} {decl}")
        self.conn.commit()

    # --- candidatos -------------------------------------------------------
    def insert_candidate(self, c: Candidate) -> int | None:
        placeholders = ",".join("?" * len(_COLS))
        try:
            cur = self.conn.execute(
                f"INSERT INTO candidates ({','.join(_COLS)}) VALUES ({placeholders})",
                _to_row(c),
            )
        except sqlite3.IntegrityError:
            return None
        self.conn.commit()
        c.id = cur.lastrowid
        return c.id

    def get(self, cid: int) -> Candidate | None:
        row = self.conn.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
        return _from_row(row) if row else None

    def get_by_clip_id(self, clip_id: str) -> Candidate | None:
        row = self.conn.execute(
            "SELECT * FROM candidates WHERE clip_id=?", (clip_id,)).fetchone()
        return _from_row(row) if row else None

    def update(self, c: Candidate) -> None:
        if c.id is None:
            raise ValueError("candidato sem id")
        c.updated_at = utcnow()
        sets = ",".join(f"{col}=?" for col in _COLS)
        self.conn.execute(f"UPDATE candidates SET {sets} WHERE id=?", [*_to_row(c), c.id])
        self.conn.commit()

    def by_state(self, state: State, limit: int = 100) -> list[Candidate]:
        rows = self.conn.execute(
            "SELECT * FROM candidates WHERE state=? ORDER BY rank DESC, id ASC LIMIT ?",
            (state.value, limit),
        ).fetchall()
        return [_from_row(r) for r in rows]

    def channel_candidates_since(self, channel: str, since: datetime) -> list[Candidate]:
        rows = self.conn.execute(
            "SELECT * FROM candidates WHERE channel=? AND created_at>=?",
            (channel.lower(), since.isoformat()),
        ).fetchall()
        return [_from_row(r) for r in rows]

    def reset_processing(self) -> int:
        cur = self.conn.execute(
            "UPDATE candidates SET state=? WHERE state=?",
            (State.DETECTED.value, State.PROCESSING.value),
        )
        self.conn.commit()
        return cur.rowcount

    def counts_since(self, since: datetime) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT state, COUNT(*) AS n FROM candidates WHERE updated_at>=? GROUP BY state",
            (since.isoformat(),),
        ).fetchall()
        return {r["state"]: r["n"] for r in rows}

    # --- publicações ------------------------------------------------------
    def record_publication(self, candidate_id: int, platform: str, post_id: str,
                           url: str, at: datetime) -> None:
        self.conn.execute(
            "INSERT INTO publications (candidate_id, platform, post_id, url, published_at)"
            " VALUES (?,?,?,?,?)",
            (candidate_id, platform, post_id, url, at.isoformat()),
        )
        self.conn.commit()

    def publications_for(self, candidate_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM publications WHERE candidate_id=? ORDER BY id", (candidate_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def publications_count_since(self, since: datetime) -> int:
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT candidate_id) AS n FROM publications WHERE published_at>=?",
            (since.isoformat(),),
        ).fetchone()
        return int(row["n"])

    def last_publication(self) -> tuple[datetime, str] | None:
        row = self.conn.execute(
            "SELECT p.published_at, c.channel FROM publications p"
            " JOIN candidates c ON c.id = p.candidate_id"
            " ORDER BY p.published_at DESC, p.id DESC LIMIT 1"
        ).fetchone()
        return (parse_iso(row["published_at"]), row["channel"]) if row else None

    # --- blocklist --------------------------------------------------------
    def block(self, channel: str, reason: str = "") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO blocklist (channel, reason, added_at) VALUES (?,?,?)",
            (channel.lower(), reason, utcnow().isoformat()),
        )
        self.conn.commit()

    def unblock(self, channel: str) -> None:
        self.conn.execute("DELETE FROM blocklist WHERE channel=?", (channel.lower(),))
        self.conn.commit()

    def is_blocked(self, channel: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM blocklist WHERE channel=?", (channel.lower(),)
        ).fetchone()
        return row is not None

    def blocked_channels(self) -> set[str]:
        return {r["channel"] for r in self.conn.execute("SELECT channel FROM blocklist")}

    # --- kv ---------------------------------------------------------------
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def kv_set(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?,?)", (key, value))
        self.conn.commit()

    def kv_incr_float(self, key: str, delta: float) -> float:
        new = float(self.kv_get(key, "0") or 0) + delta
        self.kv_set(key, repr(new))
        return new
