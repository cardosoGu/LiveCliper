from datetime import timedelta
from pathlib import Path

from factories import make_candidate
from liveclips.core.config import load_settings
from liveclips.ai.evaluator import AIUnavailable
from liveclips.ai.verdict import Verdict
from liveclips.core.models import State
from liveclips.processing.pipeline import Pipeline


def verdict(score=8.0, safe=True):
    return Verdict(score=score, safe=safe, reason="r", title="t", caption="c",
                   hashtags=["#GTA6"])


class FakeEvaluator:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def evaluate(self, video, raw, meta):
        self.calls += 1
        self.videos = (video, raw)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make_pipeline(db, tmp_path, result, with_media=True, **env):
    cfg = load_settings(env)
    c = make_candidate()
    if with_media:
        raw = tmp_path / "raw.mp4"
        raw.write_bytes(b"raw")
        c.raw_path, c.transcript = str(raw), "olha"
    db.insert_candidate(c)
    now = c.created_at + timedelta(minutes=2)
    p = Pipeline(db, cfg, FakeEvaluator(result), tmp_path, now_fn=lambda: now)
    return p, c


async def test_good_clip_awaits_approval(db, tmp_path: Path):
    p, c = make_pipeline(db, tmp_path, verdict(8.0))
    assert await p.tick() == 1
    got = db.get(c.id)
    assert got.state is State.AWAITING_APPROVAL and got.ai_score == 8.0
    # o vertical só é renderizado depois de aprovado (no scheduler); a IA vê o original
    assert got.vertical_path is None
    assert p.evaluator.videos == (Path(got.raw_path), Path(got.raw_path))
    assert p.evals_today() == 1


async def test_unsafe_is_rejected(db, tmp_path):
    p, c = make_pipeline(db, tmp_path, verdict(9.5, safe=False))
    await p.tick()
    assert db.get(c.id).state is State.REJECTED_AI


async def test_low_score_respects_kv_override(db, tmp_path):
    p, c = make_pipeline(db, tmp_path, verdict(7.5))
    db.kv_set("min_score", "8")
    await p.tick()
    assert db.get(c.id).state is State.REJECTED_AI


async def test_ai_unavailable_keeps_detected_and_media(db, tmp_path):
    p, c = make_pipeline(db, tmp_path, AIUnavailable("x"))
    await p.tick()
    got = db.get(c.id)
    assert got.state is State.DETECTED and p.ai_down
    assert Path(got.raw_path).exists() and got.transcript == "olha"
    assert p.evals_today() == 0


async def test_budget_exhausted_does_nothing(db, tmp_path):
    p, c = make_pipeline(db, tmp_path, verdict(), AI_DAILY_BUDGET="0")
    assert await p.tick() == 0
    assert p.evaluator.calls == 0


async def test_missing_media_fails_without_downloading(db, tmp_path):
    p, c = make_pipeline(db, tmp_path, verdict(8.0), with_media=False)
    await p.tick()
    got = db.get(c.id)
    assert got.state is State.FAILED and got.reason == "trecho sumiu antes da avaliação"
    assert p.evaluator.calls == 0 and not (tmp_path / str(c.id)).exists()


async def test_old_candidate_is_still_evaluated(db, tmp_path):
    p, c = make_pipeline(db, tmp_path, verdict(8.0))
    p.now_fn = lambda: c.created_at + timedelta(days=2)
    await p.tick()
    assert db.get(c.id).state is State.AWAITING_APPROVAL


def test_backoff_grows_while_ai_is_down_and_resets(db, tmp_path):
    p, _ = make_pipeline(db, tmp_path, verdict())
    p.ai_down = True
    assert [p.next_delay() for _ in range(6)] == [60, 120, 240, 480, 900, 900]
    p.ai_down = False
    assert p.next_delay() == 5
    p.ai_down = True
    assert p.next_delay() == 60
