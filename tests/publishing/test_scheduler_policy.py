from datetime import timedelta

from factories import make_candidate
from liveclips.core.models import is_expired
from liveclips.publishing.policy import can_post_now, daily_limit, pick_next


def test_daily_limit_ramp():
    assert [daily_limit(d, 8, 2, 20) for d in range(8)] == [8, 10, 12, 14, 16, 18, 20, 20]
    assert daily_limit(-1, 8, 2, 20) == 8


def test_can_post_now():
    c = make_candidate()
    now = c.created_at
    assert can_post_now(now, None, 0, 8, 45)
    assert not can_post_now(now, now - timedelta(minutes=30), 1, 8, 45)
    assert can_post_now(now, now - timedelta(minutes=45), 1, 8, 45)
    assert not can_post_now(now, None, 8, 8, 45)


def test_pick_next_prefers_score_and_skips_same_channel():
    a = make_candidate(clip_id="a", channel="x", ai_score=9.0)
    b = make_candidate(clip_id="b", channel="y", ai_score=8.0)
    assert pick_next([b, a], None) is a
    assert pick_next([b, a], "x") is b
    assert pick_next([a], "x") is None


def test_is_expired():
    c = make_candidate()
    assert not is_expired(c, c.created_at + timedelta(minutes=89), 90)
    assert is_expired(c, c.created_at + timedelta(minutes=91), 90)
