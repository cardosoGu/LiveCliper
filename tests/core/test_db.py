from datetime import timedelta

from factories import make_candidate
from liveclips.core.models import State


def test_insert_and_get_roundtrip(db):
    c = make_candidate()
    cid = db.insert_candidate(c)
    got = db.get(cid)
    assert got.id == cid
    assert got.created_at == c.created_at
    assert got.state is State.DETECTED
    assert got.channel == "gaules"


def test_duplicate_clip_id_returns_none(db):
    assert db.insert_candidate(make_candidate()) is not None
    assert db.insert_candidate(make_candidate()) is None


def test_by_state_orders_by_rank(db):
    db.insert_candidate(make_candidate(clip_id="a", rank=1))
    db.insert_candidate(make_candidate(clip_id="b", rank=9))
    db.insert_candidate(make_candidate(clip_id="c", rank=5, state=State.QUEUED))
    assert [c.clip_id for c in db.by_state(State.DETECTED)] == ["b", "a"]


def test_update_changes_state(db):
    c = make_candidate()
    db.insert_candidate(c)
    c.state = State.QUEUED
    c.ai_score = 8.0
    db.update(c)
    got = db.get(c.id)
    assert got.state is State.QUEUED and got.ai_score == 8.0


def test_last_publication_and_count(db):
    c1 = make_candidate(clip_id="a", channel="x")
    c2 = make_candidate(clip_id="b", channel="y")
    db.insert_candidate(c1)
    db.insert_candidate(c2)
    t = c1.created_at
    db.record_publication(c1.id, "instagram", "p1", "u1", t)
    db.record_publication(c2.id, "instagram", "p2", "u2", t + timedelta(minutes=50))
    db.record_publication(c2.id, "tiktok", "p3", "", t + timedelta(minutes=50))
    assert db.last_publication() == (t + timedelta(minutes=50), "y")
    assert db.publications_count_since(t) == 2
    assert len(db.publications_for(c2.id)) == 2


def test_blocklist_is_case_insensitive(db):
    db.block("Gaules", "teste")
    assert db.is_blocked("gaules")
    assert db.blocked_channels() == {"gaules"}
    db.unblock("GAULES")
    assert not db.is_blocked("gaules")


def test_kv_and_incr(db):
    assert db.kv_get("x") is None
    assert db.kv_get("x", "d") == "d"
    db.kv_set("x", "1")
    assert db.kv_get("x") == "1"
    db.kv_incr_float("spend", 0.5)
    assert db.kv_incr_float("spend", 0.25) == 0.75


def test_reset_processing(db):
    c = make_candidate(state=State.PROCESSING)
    db.insert_candidate(c)
    assert db.reset_processing() == 1
    assert db.get(c.id).state is State.DETECTED


def test_approval_fields_roundtrip(db):
    c = make_candidate(state=State.AWAITING_APPROVAL, approval_message_id=123, priority=1)
    db.insert_candidate(c)
    got = db.get(c.id)
    assert got.state is State.AWAITING_APPROVAL
    assert got.approval_message_id == 123 and got.priority == 1


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3

    from liveclips.core.db import DB

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE candidates (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " platform TEXT NOT NULL, source TEXT NOT NULL, clip_id TEXT NOT NULL UNIQUE,"
        " clip_url TEXT NOT NULL, channel TEXT NOT NULL, created_at TEXT NOT NULL,"
        " duration REAL NOT NULL, title TEXT NOT NULL, viewers INTEGER NOT NULL,"
        " view_count INTEGER NOT NULL, spike_score REAL NOT NULL, rank REAL NOT NULL,"
        " state TEXT NOT NULL, raw_path TEXT, vertical_path TEXT, transcript TEXT,"
        " ai_score REAL, ai_json TEXT, reason TEXT, updated_at TEXT NOT NULL)")
    conn.commit()
    conn.close()
    db = DB(str(path))
    c = make_candidate(priority=1, approval_message_id=7)
    db.insert_candidate(c)
    got = db.get(c.id)
    assert got.priority == 1 and got.approval_message_id == 7


def test_dry_run_publications_do_not_count(db):
    from datetime import datetime, timezone
    cid = db.insert_candidate(make_candidate())
    at = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    db.record_publication(cid, "dry_run", "x", "", at)
    since = datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc)
    assert db.publications_count_since(since) == 0
    assert db.last_publication() is None
    db.record_publication(cid, "instagram", "y", "u", at)
    assert db.publications_count_since(since) == 1
    assert db.last_publication() is not None


def test_ondemand_fields_roundtrip(db):
    c = make_candidate(source="ondemand", job_id=3, start_s=12.5, end_s=60.0)
    db.insert_candidate(c)
    got = db.get(c.id)
    assert (got.job_id, got.start_s, got.end_s) == (3, 12.5, 60.0)
    assert [x.id for x in db.job_candidates(3)] == [c.id]


def test_jobs_queue(db):
    a = db.create_job("https://youtu.be/a", "foca no convidado", 10, 20)
    b = db.create_job("https://youtu.be/b", "", None, None)
    assert db.get_job(a)["state"] == "queued" and db.get_job(a)["instructions"] == "foca no convidado"
    assert db.next_job()["id"] == a
    db.update_job(a, state="running", step="audio", progress="baixando áudio…")
    assert db.next_job()["id"] == b
    assert [j["id"] for j in db.jobs_in(["running"])] == [a]
    assert db.reset_running_jobs() == 1 and db.get_job(a)["state"] == "queued"
    import pytest
    with pytest.raises(ValueError):
        db.update_job(a, url="x")  # campo não permitido
