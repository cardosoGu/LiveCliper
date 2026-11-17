from datetime import timedelta
from pathlib import Path

from conftest import make_candidate
from liveclips.config import load_settings
from liveclips.evaluator import AIUnavailable, Verdict
from liveclips.models import State
from liveclips.pipeline import MediaOps, Pipeline


def verdict(score=8.0, safe=True):
    return Verdict(score=score, safe=safe, reason="r", title="t", caption="c",
                   hashtags=["#GTA6"])


class FakeEvaluator:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def evaluate(self, video, raw, meta):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def fake_ops(fail_download=False):
    async def download(url, dest):
        if fail_download:
            raise RuntimeError("404")
        dest.write_bytes(b"raw")
        return dest

    async def extract_audio(src, dst):
        dst.write_bytes(b"a")
        return dst

    async def transcribe(audio, key, http):
        return [{"start": 0, "end": 1, "text": "olha"}]

    async def render(src, dst, credit, srt):
        assert credit == "twitch.tv/gaules" and srt is not None
        dst.write_bytes(b"v")
        return dst

    return MediaOps(download, extract_audio, transcribe, render)


def make_pipeline(db, tmp_path, result, ops=None, **env):
    cfg = load_settings(env)
    c = make_candidate()
    db.insert_candidate(c)
    now = c.created_at + timedelta(minutes=2)
    p = Pipeline(db, cfg, FakeEvaluator(result), None, tmp_path, ops or fake_ops(),
                 now_fn=lambda: now)
    return p, c


async def test_good_clip_is_queued(db, tmp_path: Path):
    p, c = make_pipeline(db, tmp_path, verdict(8.0))
    assert await p.tick() == 1
    got = db.get(c.id)
    assert got.state is State.QUEUED and got.ai_score == 8.0
    assert got.transcript == "olha" and Path(got.vertical_path).exists()
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
    assert Path(got.vertical_path).exists()
    assert p.evals_today() == 0


async def test_budget_exhausted_does_nothing(db, tmp_path):
    p, c = make_pipeline(db, tmp_path, verdict(), AI_DAILY_BUDGET="0")
    assert await p.tick() == 0
    assert p.evaluator.calls == 0


async def test_low_rank_rejected(db, tmp_path):
    p, c = make_pipeline(db, tmp_path, verdict(), RANK_THRESHOLD="50")
    await p.tick()
    assert db.get(c.id).state is State.REJECTED_RANK


async def test_media_error_marks_failed(db, tmp_path):
    p, c = make_pipeline(db, tmp_path, verdict(), ops=fake_ops(fail_download=True))
    await p.tick()
    got = db.get(c.id)
    assert got.state is State.FAILED and "404" in got.reason


async def test_old_candidate_expires(db, tmp_path):
    p, c = make_pipeline(db, tmp_path, verdict())
    later = c.created_at + timedelta(minutes=200)
    p.now_fn = lambda: later
    await p.tick()
    assert db.get(c.id).state is State.EXPIRED
