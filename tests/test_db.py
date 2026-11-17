from datetime import timedelta

from conftest import make_candidate
from liveclips.models import State


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
