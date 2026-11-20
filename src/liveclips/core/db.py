"""Persistência em SQLite: candidatos, publicações, jobs e um kv simples."""
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
  extra_clip_url TEXT, approval_message_id INTEGER, priority INTEGER NOT NULL DEFAULT 0,
  job_id INTEGER, start_s REAL, end_s REAL,
  series_id TEXT, part INTEGER, parts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cand_state ON candidates(state);
CREATE INDEX IF NOT EXISTS idx_cand_channel ON candidates(channel, created_at);
CREATE TABLE IF NOT EXISTS publications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL, platform TEXT NOT NULL,
  post_id TEXT, url TEXT, published_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL, instructions TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL, step TEXT NOT NULL DEFAULT '', progress TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '', channel_id INTEGER, message_id INTEGER,
  thread_id INTEGER, status_message_id INTEGER, error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""

_COLS = [
    "platform", "source", "clip_id", "clip_url", "channel", "created_at",
    "duration", "title", "viewers", "view_count", "spike_score", "rank",
    "state", "raw_path", "vertical_path", "transcript", "ai_score", "ai_json",
    "reason", "updated_at", "extra_clip_url", "approval_message_id", "priority",
    "job_id", "start_s", "end_s", "series_id", "part", "parts",
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
        "job_id": "INTEGER",
        "start_s": "REAL",
        "end_s": "REAL",
        "series_id": "TEXT",
        "part": "INTEGER",
        "parts": "INTEGER",
    }

    def _migrate(self) -> None:
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(candidates)")}
        for name, decl in self._NEW_COLUMNS.items():
            if name not in cols:
                self.conn.execute(f"ALTER TABLE candidates ADD COLUMN {name} {decl}")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_cand_job ON candidates(job_id)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cand_series ON candidates(series_id)")
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

    def awaiting_cards(self, posted: bool, limit: int = 50) -> list[Candidate]:
        """Candidatos aguardando aprovação, com (posted) ou sem card no #aprovação."""
        cond = "IS NOT NULL" if posted else "IS NULL"
        rows = self.conn.execute(
            "SELECT * FROM candidates WHERE state=?"
            f" AND approval_message_id {cond} ORDER BY id ASC LIMIT ?",
            (State.AWAITING_APPROVAL.value, limit),
        ).fetchall()
        return [_from_row(r) for r in rows]

    def protected_media_ids(self) -> set[int]:
        """Clipes cujo vídeo ainda será usado (aguardando ou aprovados). Os cortes do sob
        demanda também: o trecho baixado não pode ser baixado de novo como um clipe."""
        rows = self.conn.execute(
            "SELECT id FROM candidates WHERE state IN (?,?) OR (source='ondemand' AND "
            "(state IN (?,?) OR (state=? AND reason=?)))",
            (State.AWAITING_APPROVAL.value, State.APPROVED.value, State.DETECTED.value,
             State.PROCESSING.value, State.FAILED.value, "recortando (sob demanda)"),
        ).fetchall()
        return {int(r["id"]) for r in rows}

    def reset_processing(self) -> int:
        cur = self.conn.execute(
            "UPDATE candidates SET state=? WHERE state=?",
            (State.DETECTED.value, State.PROCESSING.value),
        )
        self.conn.commit()
        return cur.rowcount

    def retire_legacy_candidates(self) -> int:
        """Descarta o que sobrou do tempo real (removido): nunca deve ser avaliado nem postado."""
        cur = self.conn.execute(
            "UPDATE candidates SET state=?, reason=? WHERE source<>'ondemand' AND state IN (?,?,?)",
            (State.DISCARDED.value, "tempo real removido", State.DETECTED.value,
             State.PROCESSING.value, State.AWAITING_APPROVAL.value))
        self.conn.commit()
        return cur.rowcount

    def counts_since(self, since: datetime) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT state, COUNT(*) AS n FROM candidates WHERE updated_at>=? GROUP BY state",
            (since.isoformat(),),
        ).fetchall()
        return {r["state"]: r["n"] for r in rows}

    # --- jobs sob demanda -------------------------------------------------
    _JOB_FIELDS = {"state", "step", "progress", "title", "thread_id", "status_message_id",
                   "error"}

    def create_job(self, url: str, instructions: str, channel_id: int | None,
                   message_id: int | None) -> int:
        now = utcnow().isoformat()
        cur = self.conn.execute(
            "INSERT INTO jobs (url, instructions, state, channel_id, message_id, created_at,"
            " updated_at) VALUES (?,?,?,?,?,?,?)",
            (url, instructions, "queued", channel_id, message_id, now, now))
        self.conn.commit()
        return int(cur.lastrowid)

    def get_job(self, job_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def update_job(self, job_id: int, **fields) -> None:
        bad = set(fields) - self._JOB_FIELDS
        if bad:
            raise ValueError(f"campos inválidos: {bad}")
        fields["updated_at"] = utcnow().isoformat()
        sets = ",".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", [*fields.values(), job_id])
        self.conn.commit()

    def next_job(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
        return dict(row) if row else None

    def jobs_in(self, states: list[str]) -> list[dict]:
        marks = ",".join("?" * len(states))
        rows = self.conn.execute(
            f"SELECT * FROM jobs WHERE state IN ({marks}) ORDER BY id", states).fetchall()
        return [dict(r) for r in rows]

    def reset_running_jobs(self) -> int:
        cur = self.conn.execute("UPDATE jobs SET state='queued' WHERE state='running'")
        self.conn.commit()
        return cur.rowcount

    def job_candidates(self, job_id: int) -> list[Candidate]:
        rows = self.conn.execute(
            "SELECT * FROM candidates WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
        return [_from_row(r) for r in rows]

    def series_parts(self, series_id: str) -> list[Candidate]:
        rows = self.conn.execute(
            "SELECT * FROM candidates WHERE series_id=? ORDER BY part, id", (series_id,)
        ).fetchall()
        return [_from_row(r) for r in rows]

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
            "SELECT COUNT(DISTINCT candidate_id) AS n FROM publications WHERE published_at>=?"
            " AND platform<>'dry_run'",
            (since.isoformat(),),
        ).fetchone()
        return int(row["n"])

    def last_publication(self) -> tuple[datetime, str] | None:
        row = self.conn.execute(
            "SELECT p.published_at, c.channel FROM publications p"
            " JOIN candidates c ON c.id = p.candidate_id WHERE p.platform<>'dry_run'"
            " ORDER BY p.published_at DESC, p.id DESC LIMIT 1"
        ).fetchone()
        return (parse_iso(row["published_at"]), row["channel"]) if row else None

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
