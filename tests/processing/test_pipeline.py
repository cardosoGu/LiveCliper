from datetime import timedelta
from pathlib import Path

from factories import make_candidate
from liveclips.core.config import load_settings
from liveclips.ai.evaluator import AIUnavailable
from liveclips.ai.verdict import Verdict
from liveclips.core.models import State
from liveclips.processing.pipeline import MediaOps, Pipeline
from liveclips.processing.transcribe import Transcript


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


def fake_ops(fail_download=False, speech=("olha", "pt")):
    async def download(url, dest):
        if fail_download:
            raise RuntimeError("404")
        dest.write_bytes(b"raw")
        return dest

    async def extract_audio(src, dst):
        dst.write_bytes(b"a")
        return dst

    async def transcribe(audio, key, http):
        text, lang = speech
        return Transcript([{"start": 0, "end": 1, "text": text}], lang)

    async def probe(path):
        return 1920, 1080

    return MediaOps(download, extract_audio, transcribe, probe)


def make_pipeline(db, tmp_path, result, ops=None, **env):
    cfg = load_settings(env)
    c = make_candidate()
    db.insert_candidate(c)
    now = c.created_at + timedelta(minutes=2)
    p = Pipeline(db, cfg, FakeEvaluator(result), None, tmp_path, ops or fake_ops(),
                 now_fn=lambda: now)
    return p, c


async def test_good_clip_awaits_approval(db, tmp_path: Path):
    p, c = make_pipeline(db, tmp_path, verdict(8.0))
    assert await p.tick() == 1
    got = db.get(c.id)
    assert got.state is State.AWAITING_APPROVAL and got.ai_score == 8.0
    assert got.transcript == "olha" and Path(got.raw_path).exists()
    # o vertical só é renderizado depois de aprovado (no scheduler); a IA vê o original
    assert got.vertical_path is None
    assert p.evaluator.videos == (Path(got.raw_path), Path(got.raw_path))
    assert "Dialogue:" in (Path(got.raw_path).parent / "subs.ass").read_text(encoding="utf-8")
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


async def test_foreign_speech_rejected_before_ai(db, tmp_path):
    ops = fake_ops(speech=("oh my god he just fell off the roof", "english"))
    p, c = make_pipeline(db, tmp_path, verdict(9.0), ops=ops)
    await p.tick()
    got = db.get(c.id)
    assert got.state is State.REJECTED_AI and "english" in got.reason
    assert p.evaluator.calls == 0 and p.evals_today() == 0
    assert got.vertical_path is None  # nem renderiza
    assert not (Path(got.raw_path).parent / "subs.ass").exists()


def test_backoff_grows_while_ai_is_down_and_resets(db, tmp_path):
    p, _ = make_pipeline(db, tmp_path, verdict())
    p.ai_down = True
    assert [p.next_delay() for _ in range(6)] == [60, 120, 240, 480, 900, 900]
    p.ai_down = False
    assert p.next_delay() == 5
    p.ai_down = True
    assert p.next_delay() == 60


async def test_manual_test_clip_never_awaits_approval(db, tmp_path):
    cfg = load_settings({})
    c = make_candidate(source="manual_test", state=State.PROCESSING)
    db.insert_candidate(c)
    p = Pipeline(db, cfg, FakeEvaluator(verdict(8.0)), None, tmp_path, fake_ops())
    await p.process(c)
    got = db.get(c.id)
    assert got.state is State.TESTED and got.ai_score == 8.0
    assert db.by_state(State.AWAITING_APPROVAL) == []


async def test_ondemand_candidate_never_expires(db, tmp_path):
    from datetime import timedelta
    p, c = make_pipeline(db, tmp_path, verdict(8.0))
    c.source = "ondemand"
    db.update(c)
    later = c.created_at + timedelta(days=2)
    p.now_fn = lambda: later
    await p.tick()
    assert db.get(c.id).state is not State.EXPIRED
