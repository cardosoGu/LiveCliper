from datetime import timedelta

from factories import make_candidate
from liveclips.capture.intake import admit, candidate_from_twitch_clip
from liveclips.core.models import State
from liveclips.capture.ranking import compute_rank, find_overlap


def test_rank_chat_spike_uses_spike_score():
    c = make_candidate(source="chat_spike", spike_score=12.5)
    assert compute_rank(c, c.created_at) == 12.5


def test_rank_public_uses_views_per_minute():
    c = make_candidate(source="twitch_public", view_count=100, viewers=1000)
    now = c.created_at + timedelta(minutes=10)
    assert compute_rank(c, now) == 10 * 3.0


def test_overlap_same_channel_within_window():
    a = make_candidate(clip_id="a")
    b = make_candidate(clip_id="b", created_at=a.created_at + timedelta(seconds=20))
    far = make_candidate(clip_id="c", created_at=a.created_at + timedelta(seconds=90))
    other = make_candidate(clip_id="d", channel="outro")
    assert find_overlap(b, [a]) is a
    assert find_overlap(far, [a]) is None
    assert find_overlap(other, [a]) is None


def test_admit_rejects_blocked(db):
    db.block("gaules")
    assert admit(db, make_candidate()) is False


def test_admit_replaces_weaker_duplicate(db):
    weak = make_candidate(clip_id="a", spike_score=5)
    assert admit(db, weak, weak.created_at)
    strong = make_candidate(clip_id="b", spike_score=20,
                            created_at=weak.created_at + timedelta(seconds=10))
    assert admit(db, strong, strong.created_at)
    assert db.get(weak.id).state is State.REJECTED_RANK


def test_admit_refuses_stronger_existing(db):
    strong = make_candidate(clip_id="a", spike_score=20)
    assert admit(db, strong, strong.created_at)
    weak = make_candidate(clip_id="b", spike_score=5,
                          created_at=strong.created_at + timedelta(seconds=10))
    assert admit(db, weak, weak.created_at) is False


def test_candidate_from_twitch_clip():
    clip = {"id": "Abc", "url": "https://clips.twitch.tv/Abc", "broadcaster_name": "Gaules",
            "created_at": "2026-11-19T03:00:00Z", "duration": 28.4, "title": "kkk",
            "view_count": 7}
    c = candidate_from_twitch_clip(clip, "twitch_public", viewers=500)
    assert c.clip_id == "twitch:Abc" and c.channel == "gaules"
    assert c.created_at.tzinfo is not None and c.view_count == 7
