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


def make_series(db, tmp_path, result, n=3, skip_raw=None, only_parts=None, **env):
    cfg = load_settings(env)
    cs = []
    for i in range(1, n + 1):
        c = make_candidate(clip_id=f"twitch:s{i}", series_id="ser", part=i, parts=n)
        if i != skip_raw:
            raw = tmp_path / f"raw{i}.mp4"
            raw.write_bytes(b"raw")
            c.raw_path, c.transcript = str(raw), f"fala{i}"
        if only_parts is None or i in only_parts:
            db.insert_candidate(c)
        cs.append(c)
    now = cs[0].created_at + timedelta(minutes=2)
    return Pipeline(db, cfg, FakeEvaluator(result), tmp_path, now_fn=lambda: now)


class MetaEvaluator(FakeEvaluator):
    async def evaluate(self, video, raw, meta):
        self.meta = meta
        return await super().evaluate(video, raw, meta)


def series_verdict(score=8.0):
    v = verdict(score)
    v.series_recap = "No capítulo anterior"
    return v


async def test_series_is_evaluated_once_and_decision_copied(db, tmp_path):
    p = make_series(db, tmp_path, series_verdict(8.0))
    p.evaluator = MetaEvaluator(series_verdict(8.0))
    assert await p.tick() == 1
    assert p.evaluator.calls == 1
    assert p.evaluator.meta["parts"] == 3
    assert p.evaluator.meta["transcript"] == "fala1 … fala2 … fala3"
    parts = db.series_parts("ser")
    assert all(x.state is State.AWAITING_APPROVAL and x.ai_score == 8.0 for x in parts)
    assert '"title":"t"' in parts[0].ai_json.replace(" ", "")
    assert Verdict.model_validate_json(parts[1].ai_json).title == "No capítulo anterior"
    assert Verdict.model_validate_json(parts[2].ai_json).title == "No capítulo anterior"
    assert p.evals_today() == 1


async def test_series_part_title_fallback(db, tmp_path):
    p = make_series(db, tmp_path, verdict(8.0))
    await p.tick()
    parts = db.series_parts("ser")
    assert Verdict.model_validate_json(parts[1].ai_json).title == "PARTE 2"
    assert Verdict.model_validate_json(parts[2].ai_json).title == "PARTE 3"


async def test_series_low_score_rejects_all(db, tmp_path):
    p = make_series(db, tmp_path, verdict(3.0))
    await p.tick()
    assert all(x.state is State.REJECTED_AI for x in db.series_parts("ser"))


async def test_series_missing_part_media_fails_all(db, tmp_path):
    p = make_series(db, tmp_path, verdict(8.0), skip_raw=2)
    await p.tick()
    parts = db.series_parts("ser")
    assert all(x.state is State.FAILED and x.reason == "trecho sumiu antes da avaliação"
               for x in parts)
    assert p.evaluator.calls == 0


async def test_later_parts_alone_are_never_evaluated(db, tmp_path):
    p = make_series(db, tmp_path, verdict(8.0), only_parts={2, 3})
    assert await p.tick() == 0
    assert p.evaluator.calls == 0


async def test_series_ai_unavailable_returns_to_detected(db, tmp_path):
    p = make_series(db, tmp_path, AIUnavailable("x"))
    await p.tick()
    assert p.ai_down
    assert all(x.state is State.DETECTED for x in db.series_parts("ser"))


async def test_series_without_all_part_rows_fails_as_incomplete(db, tmp_path):
    p = make_series(db, tmp_path, verdict(8.0), n=2, only_parts={1})
    await p.tick()
    [one] = db.series_parts("ser")
    assert one.state is State.FAILED and one.reason == "série incompleta"
    assert p.evaluator.calls == 0 and p.evals_today() == 0


async def test_orphan_part_inherits_decision(db, tmp_path):
    p = make_series(db, tmp_path, verdict(8.0), n=2)
    one, two = db.series_parts("ser")
    v = series_verdict(8.0)
    one.state, one.ai_score, one.ai_json, one.reason = (
        State.AWAITING_APPROVAL, 8.0, v.model_dump_json(), "ok")
    db.update(one)
    assert await p.tick() == 0
    assert p.evaluator.calls == 0
    two = db.series_parts("ser")[1]
    assert two.state is State.AWAITING_APPROVAL and two.ai_score == 8.0 and two.reason == "ok"
    assert Verdict.model_validate_json(two.ai_json).title == "No capítulo anterior"


async def test_orphan_part_fails_when_part1_failed(db, tmp_path):
    p = make_series(db, tmp_path, verdict(8.0), n=2)
    one = db.series_parts("ser")[0]
    one.state = State.FAILED
    db.update(one)
    await p.tick()
    two = db.series_parts("ser")[1]
    assert two.state is State.FAILED and two.reason == "a série já foi decidida sem esta parte"
    assert p.evaluator.calls == 0
