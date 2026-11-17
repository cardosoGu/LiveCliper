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
    assert db.get(weak.id).state is State.DUPLICATE


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


def test_cross_source_window_is_wider():
    bot = make_candidate(clip_id="a", source="chat_spike")
    viewer = make_candidate(clip_id="b", source="twitch_public",
                            created_at=bot.created_at + timedelta(seconds=90))
    same_src = make_candidate(clip_id="c", source="chat_spike",
                              created_at=bot.created_at + timedelta(seconds=90))
    assert find_overlap(viewer, [bot]) is bot
    assert find_overlap(same_src, [bot]) is None


def test_seen_again_public_clip_is_reranked_and_revived(db):
    early = make_candidate(clip_id="twitch:x", source="twitch_public", view_count=1,
                           viewers=2000, spike_score=0)
    now = early.created_at + timedelta(minutes=2)
    assert admit(db, early, now)
    stored = db.get_by_clip_id("twitch:x")
    stored.state = State.REJECTED_RANK
    db.update(stored)
    later = make_candidate(clip_id="twitch:x", source="twitch_public", view_count=300,
                           viewers=2000, spike_score=0)
    assert admit(db, later, now + timedelta(minutes=6)) is False  # não é candidato novo
    got = db.get_by_clip_id("twitch:x")
    assert got.state is State.DETECTED and got.view_count == 300 and got.rank > stored.rank


def test_seen_again_does_not_touch_finished_candidates(db):
    c = make_candidate(clip_id="twitch:y", source="twitch_public", state=State.PUBLISHED)
    db.insert_candidate(c)
    admit(db, make_candidate(clip_id="twitch:y", source="twitch_public", view_count=999))
    assert db.get(c.id).state is State.PUBLISHED and db.get(c.id).view_count == 0
