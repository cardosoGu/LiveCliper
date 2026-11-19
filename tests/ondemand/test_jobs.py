import json
from pathlib import Path

import numpy as np

from liveclips.ai.evaluator import AIUnavailable
from liveclips.core.config import load_settings
from liveclips.core.models import State
from liveclips.ondemand.ingest import VideoMeta
from liveclips.ondemand.jobs import OnDemandOps, OnDemandRunner
from liveclips.ondemand.topics import Topic
from liveclips.processing.transcribe import Transcript


class _Log(list):
    """Lista que também é chamável: o runner chama notifier.progress(...) e o teste lê a lista."""

    def __init__(self, shape):
        super().__init__()
        self.shape = shape

    async def __call__(self, job, *args):
        self.append((job["id"], *args))


class Notes:
    def __init__(self):
        self.progress, self.finished = _Log("p"), _Log("f")


def words(n=200):
    out, t = [], 0.0
    for i in range(n):
        out.append({"word": f"w{i}" + ("." if i % 5 == 4 else ""), "start": t, "end": t + 0.5})
        t += 0.6
    return out


def make(db, tmp_path, meta=None, topics_exc=None, calls=None):
    calls = calls if calls is not None else []

    async def probe(url):
        calls.append("probe")
        return meta or VideoMeta(url, "Podpah #1", "Podpah", "youtube", 600.0, False, None, "x",
                                  "Podpah")

    async def download_audio(url, dest):
        calls.append("audio")
        dest.write_bytes(b"a")
        return dest

    async def download_section(url, s, e, dest):
        calls.append(("section", s, e))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"v")
        return dest

    async def fetch_chat(url, work):
        return []

    async def transcribe(audio, key, http, work):
        calls.append("transcribe")
        return Transcript([{"start": 0, "end": 120, "text": "x"}], "pt", words())

    async def rms(audio, work):
        return np.full(120, -30.0)

    async def topics(cfg, title, ws, peaks, instr, db=None):
        calls.append("topics")
        if topics_exc:
            raise topics_exc
        return [Topic(peak=40.0, start=30.0, end=60.0, why="boa")]

    async def probe_dims(path):
        return 1920, 1080

    ops = OnDemandOps(probe, download_audio, download_section, fetch_chat, transcribe, rms,
                      topics, probe_dims)
    notes = Notes()
    r = OnDemandRunner(db, load_settings({}), None, tmp_path / "jobs", tmp_path / "media",
                       notes, ops)
    return r, notes, calls


async def test_happy_path_creates_ready_candidates(db, tmp_path):
    r, notes, calls = make(db, tmp_path)
    jid = r.enqueue("https://youtu.be/x", "foca", 1, 2)
    await r.process(db.get_job(jid))
    job = db.get_job(jid)
    assert job["state"] == "evaluating" and job["title"] == "Podpah #1"
    [c] = db.job_candidates(jid)
    assert c.state is State.DETECTED and c.source == "ondemand" and c.rank == 1000.0
    assert c.platform == "youtube" and c.channel == "Podpah"
    assert c.start_s <= 34.0 and c.end_s >= 43.0
    raw = Path(c.raw_path)
    assert raw.parent.name == str(c.id) and raw.exists()
    assert (raw.parent / "subs.ass").exists() and c.transcript
    assert any("recortando" in t for _, t in notes.progress)


async def test_candidate_credit_uses_handle_or_honest_fallback(db, tmp_path):
    named = VideoMeta("u", "Ep", "Inteligência Ltda", "youtube", 600.0, False, None, "x", "Podpah")
    r, _, _ = make(db, tmp_path, meta=named)
    jid = r.enqueue("u", "", None, None)
    await r.process(db.get_job(jid))
    [c] = db.job_candidates(jid)
    assert (c.platform, c.channel) == ("youtube", "Podpah")
    unknown = VideoMeta("u", "Ep", "Inteligência Ltda", "youtube", 600.0, False, None, "x")
    r2, _, _ = make(db, tmp_path / "b", meta=unknown)
    jid2 = r2.enqueue("u", "", None, None)
    await r2.process(db.get_job(jid2))
    [c2] = db.job_candidates(jid2)
    assert (c2.platform, c2.channel) == ("outro", "youtube.com")
    other = VideoMeta("u", "Ep", "X", "outro", 600.0, False, None, "x", "vimeo.com")
    r3, _, _ = make(db, tmp_path / "c", meta=other)
    jid3 = r3.enqueue("u", "", None, None)
    await r3.process(db.get_job(jid3))
    [c3] = db.job_candidates(jid3)
    assert (c3.platform, c3.channel) == ("outro", "vimeo.com")


async def test_steps_are_cached_between_runs(db, tmp_path):
    r, notes, calls = make(db, tmp_path, topics_exc=AIUnavailable("429"))
    jid = r.enqueue("u", "", None, None)
    await r.process(db.get_job(jid))
    assert db.get_job(jid)["state"] == "waiting_ai"
    calls.clear()
    r.ops.topics = make(db, tmp_path)[0].ops.topics  # a IA voltou
    db.update_job(jid, state="queued")
    await r.process(db.get_job(jid))
    assert "probe" not in calls and "audio" not in calls and "transcribe" not in calls
    assert db.get_job(jid)["state"] == "evaluating"


async def test_live_and_too_long_are_refused(db, tmp_path):
    live = VideoMeta("u", "Live", "c", "twitch", 0.0, True, None, "x")
    r, notes, _ = make(db, tmp_path, meta=live)
    jid = r.enqueue("u", "", None, None)
    await r.process(db.get_job(jid))
    assert db.get_job(jid)["state"] == "failed" and "live em andamento" in notes.finished[-1][2]
    long = VideoMeta("u", "Longo", "c", "youtube", 5 * 3600.0, False, None, "x")
    r2, notes2, _ = make(db, tmp_path, meta=long)
    jid2 = r2.enqueue("u", "", None, None)
    await r2.process(db.get_job(jid2))
    assert db.get_job(jid2)["state"] == "failed" and "4 h" in notes2.finished[-1][2]
    assert not (tmp_path / "jobs" / str(jid2)).exists()  # arquivos do job apagados


async def test_check_evaluating_closes_job_with_summary(db, tmp_path):
    r, notes, _ = make(db, tmp_path)
    jid = r.enqueue("u", "", None, None)
    await r.process(db.get_job(jid))
    [c] = db.job_candidates(jid)
    await r.check_evaluating()
    assert db.get_job(jid)["state"] == "evaluating"  # ainda não avaliado
    c.state = State.AWAITING_APPROVAL
    db.update(c)
    await r.check_evaluating()
    assert db.get_job(jid)["state"] == "done"
    assert notes.finished[-1][1] is True and "1 corte" in notes.finished[-1][2]


async def test_status_line_and_queue_position(db, tmp_path):
    r, _, _ = make(db, tmp_path)
    assert r.status_line() == "🔗 Sob demanda: livre"
    a = r.enqueue("u1", "", None, None)
    b = r.enqueue("u2", "", None, None)
    assert (r.queue_position(a), r.queue_position(b)) == (1, 2)
    db.update_job(a, state="running", title="Podpah", progress="transcrevendo…")
    assert r.status_line() == '🔗 Sob demanda: processando "Podpah" (transcrevendo…) · 1 na fila'


async def test_orphan_cut_is_retried_and_reused(db, tmp_path):
    r, notes, calls = make(db, tmp_path)
    good = r.ops.download_section

    async def boom(url, s, e, dest):
        raise RuntimeError("download caiu")

    r.ops.download_section = boom
    jid = r.enqueue("u", "", None, None)
    await r.process(db.get_job(jid))
    job = db.get_job(jid)
    assert job["state"] == "failed" and "não consegui recortar" in notes.finished[-1][2]
    [orphan] = db.job_candidates(jid)
    assert orphan.state is State.FAILED and orphan.reason == "recortando (sob demanda)"

    r.ops.download_section = good
    db.update_job(jid, state="queued")
    await r.process(db.get_job(jid))
    assert db.get_job(jid)["state"] == "evaluating"
    [c] = db.job_candidates(jid)
    assert c.id == orphan.id and c.state is State.DETECTED
    assert Path(c.raw_path).exists() and (Path(c.raw_path).parent / "subs.ass").exists()


async def test_summary_counts_failed_cuts(db, tmp_path):
    from liveclips.core.models import Candidate
    from liveclips.core.timeutil import utcnow

    r, notes, _ = make(db, tmp_path)
    jid = r.enqueue("u", "", None, None)
    await r.process(db.get_job(jid))
    [c] = db.job_candidates(jid)
    c.state = State.AWAITING_APPROVAL
    db.update(c)
    db.insert_candidate(Candidate(platform="youtube", source="ondemand", clip_id="ondemand:x:1",
                                  clip_url="u", channel="c", created_at=utcnow(), duration=10,
                                  title="t", rank=1000.0, state=State.FAILED, reason="x",
                                  job_id=jid, start_s=1.0, end_s=11.0))
    await r.check_evaluating()
    assert db.get_job(jid)["state"] == "done"
    text = notes.finished[-1][2]
    assert "1 corte" in text and "1 falharam" in text
