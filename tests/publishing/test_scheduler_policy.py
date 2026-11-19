from datetime import timedelta

from factories import make_candidate
from liveclips.publishing.policy import can_post_now


def test_can_post_now():
    c = make_candidate()
    now = c.created_at
    assert can_post_now(now, None, 0, 8, 45)
    assert not can_post_now(now, now - timedelta(minutes=30), 1, 8, 45)
    assert can_post_now(now, now - timedelta(minutes=45), 1, 8, 45)
    assert not can_post_now(now, None, 8, 8, 45)

