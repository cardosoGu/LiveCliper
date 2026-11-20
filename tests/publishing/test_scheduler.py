from datetime import timedelta
from pathlib import Path

from factories import make_candidate
from liveclips.control.runtime import RuntimeSettings
from liveclips.core.config import load_settings
from liveclips.ai.verdict import Verdict
from liveclips.core.models import State
from liveclips.publishing.platforms import PostResult, PublishError
from liveclips.publishing.scheduler import Scheduler

V = Verdict(score=8, safe=True, reason="r", title="t", caption="c", hashtags=["#GTA6"])


class Pub:
    def __init__(self, name, fail=False):
        self.name = name
        self.needs_public_url = name == "instagram"
        self.fail = fail
        self.jobs = []

    async def publish(self, job):
        self.jobs.append(job)
        if self.fail:
            raise PublishError("caiu")
        return PostResult(self.name, f"{self.name}-id", f"https://{self.name}/x")


class Media:
    def __init__(self):
        self.calls = 0

    async def expose(self, path):
        self.calls += 1
        return "https://t.example/media/x.mp4"


class Notifier:
    def __init__(self):
        self.published = []
        self.alerts = []

    async def alert(self, text):
        self.alerts.append(text)

    async def post_published(self, c, verdict, results, errors):
        self.published.append((c.id, [r.platform for r in results], errors))


def setup(db, tmp_path: Path, pubs, **env):
    cfg = load_settings(env)
    c = make_candidate(state=State.APPROVED, ai_score=8.0, ai_json=V.model_dump_json(),
                       vertical_path=str(tmp_path / "vertical.mp4"))
    (tmp_path / "vertical.mp4").write_bytes(b"v")
    db.insert_candidate(c)
    now = c.created_at + timedelta(minutes=5)
    notifier = Notifier()
    s = Scheduler(db, cfg, pubs, Media(), notifier, RuntimeSettings(db, cfg), now_fn=lambda: now)
    return s, c, notifier, now


def approved(tmp_path, clip_id, **kw):
    return make_candidate(clip_id=clip_id, channel=f"canal_{clip_id}", state=State.APPROVED,
                          ai_score=7.0, ai_json=V.model_dump_json(),
                          vertical_path=str(tmp_path / "vertical.mp4"), **kw)


async def test_publishes_and_records(db, tmp_path):
    ig = Pub("instagram")
    s, c, n, now = setup(db, tmp_path, [ig])
    assert await s.tick()
    assert db.get(c.id).state is State.PUBLISHED
    assert "twitch.tv/gaules" in ig.jobs[0].caption
    assert ig.jobs[0].video_url == "https://t.example/media/x.mp4"
    assert n.published == [(c.id, ["instagram"], [])]
    assert db.kv_get("first_post_day") is not None


async def test_paused_does_nothing(db, tmp_path):
    s, c, n, now = setup(db, tmp_path, [Pub("instagram")])
    db.kv_set("paused", "1")
    assert not await s.tick()
    assert db.get(c.id).state is State.APPROVED


async def test_interval_blocks_second_post(db, tmp_path):
    s, c, n, now = setup(db, tmp_path, [Pub("instagram")])
    await s.tick()
    other = make_candidate(clip_id="b", channel="outro", state=State.APPROVED, ai_score=9.0,
                           ai_json=V.model_dump_json(), vertical_path=c.vertical_path)
    db.insert_candidate(other)
    assert not await s.tick()  # menos de 45 min depois
    later = now + timedelta(minutes=46)
    s.now_fn = lambda: later
    other.created_at = later - timedelta(minutes=1)
    db.update(other)
    assert await s.tick()


async def test_one_platform_failing_does_not_block_other(db, tmp_path):
    s, c, n, now = setup(db, tmp_path, [Pub("instagram", fail=True), Pub("tiktok")])
    await s.tick()
    assert db.get(c.id).state is State.PUBLISHED
    assert n.published[0][1] == ["tiktok"] and "instagram: caiu" in n.published[0][2][0]


async def test_total_failure_keeps_clip_and_backs_off(db, tmp_path):
    s, c, n, now = setup(db, tmp_path, [Pub("instagram", fail=True)])
    await s.tick()
    got = db.get(c.id)
    assert got.state is State.APPROVED and "caiu" in got.reason  # clipe não é descartado
    assert n.published == [] and len(n.alerts) == 1 and "5 min" in n.alerts[0]
    assert not await s.tick()  # em espera: nem tenta
    s.now_fn = lambda: now + timedelta(minutes=6)
    await s.tick()
    assert "10 min" in n.alerts[-1]  # espera dobra


async def test_clip_marked_failed_after_max_attempts(db, tmp_path):
    from liveclips.publishing.scheduler import MAX_ATTEMPTS_PER_CLIP

    s, c, n, now = setup(db, tmp_path, [Pub("instagram", fail=True)])
    for _ in range(MAX_ATTEMPTS_PER_CLIP):
        s.retry_at = None
        await s.tick()
    assert db.get(c.id).state is State.FAILED


async def test_success_resets_backoff(db, tmp_path):
    ig = Pub("instagram", fail=True)
    s, c, n, now = setup(db, tmp_path, [ig])
    await s.tick()
    ig.fail = False
    s.retry_at = None
    assert await s.tick()
    assert s.fail_streak == 0 and db.get(c.id).state is State.PUBLISHED


async def test_no_public_url_requested_when_not_needed(db, tmp_path):
    s, c, n, now = setup(db, tmp_path, [Pub("tiktok")])
    await s.tick()
    assert s.media.calls == 0 and db.get(c.id).state is State.PUBLISHED


async def test_approved_clip_gets_split_render_before_publishing(db, tmp_path):
    from liveclips.ai.verdict import FaceCam

    calls = []
    kws = []

    async def render(src, dst, credit, subs, title, facecam, **kw):
        calls.append((src, dst, credit, subs, title, facecam))
        kws.append(kw)
        dst.write_bytes(b"final")
        return dst

    v = Verdict(score=8, safe=True, reason="r", title="Ele vai conseguir?", caption="c",
                hashtags=["#GTA6"], facecam=FaceCam(x=0, y=0.7, w=0.25, h=0.3))
    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"r")
    c = make_candidate(state=State.APPROVED, ai_score=8.0, ai_json=v.model_dump_json(),
                       raw_path=str(raw))
    db.insert_candidate(c)
    now = c.created_at + timedelta(minutes=5)
    ig = Pub("tiktok")
    cfg = load_settings({})
    s = Scheduler(db, cfg, [ig], Media(), Notifier(), RuntimeSettings(db, cfg),
                  now_fn=lambda: now, render_fn=render)
    assert await s.tick()
    [(src, dst, credit, subs, title, fc)] = calls
    assert (src, dst, subs) == (raw, tmp_path / "final.mp4", tmp_path / "subs.ass")
    assert credit == "twitch.tv/gaules" and title == "Ele vai conseguir?" and fc.y == 0.7
    assert ig.jobs[0].video_path == tmp_path / "final.mp4"
    assert db.get(c.id).state is State.PUBLISHED


async def test_render_failure_marks_failed_and_alerts(db, tmp_path):
    async def broken(*a, **kw):
        raise RuntimeError("ffmpeg caiu")

    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"r")
    c = make_candidate(state=State.APPROVED, ai_score=8.0, ai_json=V.model_dump_json(),
                       raw_path=str(raw))
    db.insert_candidate(c)
    now = c.created_at + timedelta(minutes=5)
    n, ig = Notifier(), Pub("tiktok")
    cfg = load_settings({})
    s = Scheduler(db, cfg, [ig], Media(), n, RuntimeSettings(db, cfg),
                  now_fn=lambda: now, render_fn=broken)
    await s.tick()
    got = db.get(c.id)
    assert got.state is State.FAILED and "ffmpeg caiu" in got.reason
    assert ig.jobs == [] and "ffmpeg caiu" in n.alerts[0]


async def test_platform_switched_off_is_skipped(db, tmp_path):
    ig, tt = Pub("instagram"), Pub("tiktok")
    s, c, n, now = setup(db, tmp_path, [ig, tt])
    s.runtime.set_platform("tiktok", False)
    assert await s.tick()
    assert len(ig.jobs) == 1 and tt.jobs == []


async def test_all_platforms_off_keeps_clip_waiting(db, tmp_path):
    ig = Pub("instagram")
    s, c, n, now = setup(db, tmp_path, [ig], DRY_RUN="1")
    assert await s.tick() is False
    assert db.get(c.id).state is State.APPROVED and ig.jobs == []


async def test_only_approved_clips_are_published(db, tmp_path):
    ig = Pub("instagram")
    s, c, n, now = setup(db, tmp_path, [ig])
    c.state = State.AWAITING_APPROVAL
    db.update(c)
    assert await s.tick() is False and ig.jobs == []


async def test_approved_clip_never_expires(db, tmp_path):
    s, c, n, now = setup(db, tmp_path, [Pub("instagram")])
    s.now_fn = lambda: now + timedelta(days=3)
    assert await s.tick()
    assert db.get(c.id).state is State.PUBLISHED


async def test_now_skips_interval_but_respects_daily_limit(db, tmp_path):
    ig = Pub("instagram")
    s, c, n, now = setup(db, tmp_path, [ig])
    assert await s.tick()  # primeiro post
    urgent = approved(tmp_path, "u", priority=1)
    db.insert_candidate(urgent)
    assert await s.tick()  # dentro dos 45 min, mas é "⚡ Agora"
    got = db.get(urgent.id)
    assert got.state is State.PUBLISHED and got.priority == 0
    s.runtime.set_post_max_per_day(2)
    db.insert_candidate(approved(tmp_path, "w", priority=1))
    assert await s.tick() is False  # limite diário vale até para o "agora"


async def test_queue_is_first_approved_first_published(db, tmp_path):
    ig = Pub("instagram")
    s, c, n, now = setup(db, tmp_path, [ig])
    later = approved(tmp_path, "z")
    later.ai_score = 9.9  # nota maior não fura a fila: vale a ordem de aprovação
    db.insert_candidate(later)
    assert await s.tick()
    assert db.get(c.id).state is State.PUBLISHED
    assert db.get(later.id).state is State.APPROVED


async def test_status_callback_runs_after_publishing(db, tmp_path):
    seen = []

    async def on_status(cand):
        seen.append((cand.id, cand.state))

    s, c, n, now = setup(db, tmp_path, [Pub("instagram")])
    s.on_status = on_status
    await s.tick()
    assert seen == [(c.id, State.PUBLISHED)]


async def test_partial_failure_is_kept_and_retry_publishes_one_platform(db, tmp_path):
    ig, tt = Pub("instagram"), Pub("tiktok", fail=True)
    s, c, n, now = setup(db, tmp_path, [ig, tt])
    await s.tick()
    got = db.get(c.id)
    assert got.state is State.PUBLISHED and got.reason.startswith("tiktok:")
    tt.fail = False
    msg = await s.publish_to(got, "tiktok")
    assert "TikTok" in msg or "tiktok" in msg
    assert [p["platform"] for p in db.publications_for(c.id)] == ["instagram", "tiktok"]
    assert db.get(c.id).reason is None
    assert len(ig.jobs) == 1  # o Reels não foi reenviado


async def test_retry_failure_is_reported(db, tmp_path):
    tt = Pub("tiktok", fail=True)
    s, c, n, now = setup(db, tmp_path, [Pub("instagram"), tt])
    await s.tick()
    msg = await s.publish_to(db.get(c.id), "tiktok")
    assert "caiu" in msg and db.get(c.id).reason.startswith("tiktok:")
    assert "não configurad" in await s.publish_to(db.get(c.id), "youtube")


async def test_retry_with_missing_video_keeps_published_state_and_reason(db, tmp_path):
    s, c, n, now = setup(db, tmp_path, [Pub("instagram"), Pub("tiktok")])
    c.state, c.reason = State.PUBLISHED, "tiktok: token vencido"
    c.vertical_path, c.raw_path = str(tmp_path / "sumiu.mp4"), None
    db.update(c)
    msg = await s.publish_to(db.get(c.id), "tiktok")
    assert "indisponível" in msg
    got = db.get(c.id)
    assert got.state is State.PUBLISHED and got.reason == "tiktok: token vencido"


async def test_series_part_with_podcast_verdict_passes_series_and_layout(db, tmp_path):
    from liveclips.ai.verdict import FaceCam

    got = {}

    async def render(src, dst, credit, subs, title, facecam, **kw):
        got.update(kw)
        dst.write_bytes(b"final")
        return dst

    subj = FaceCam(x=0.6, y=0.2, w=0.2, h=0.4)
    v = Verdict(score=8, safe=True, reason="r", title="t", caption="c",
                hashtags=["#x"], layout="podcast", subject=subj)
    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"r")
    c = make_candidate(state=State.APPROVED, ai_score=8.0, ai_json=v.model_dump_json(),
                       raw_path=str(raw), series_id="s1", part=2, parts=3)
    db.insert_candidate(c)
    now = c.created_at + timedelta(minutes=5)
    cfg = load_settings({})
    s = Scheduler(db, cfg, [Pub("tiktok")], Media(), Notifier(), RuntimeSettings(db, cfg),
                  now_fn=lambda: now, render_fn=render)
    assert await s.tick()
    assert got["series"] == (2, 3) and got["layout"] == "podcast" and got["subject"] == subj


def _series(db, tmp_path, states):
    parts = len(states)
    out = []
    for i, st in enumerate(states, start=1):
        c = make_candidate(clip_id=f"s-{i}", channel="gaules", state=st, ai_score=8.0,
                           ai_json=V.model_dump_json(), series_id="ser", part=i, parts=parts,
                           vertical_path=str(tmp_path / "vertical.mp4"))
        db.insert_candidate(c)
        out.append(c)
    (tmp_path / "vertical.mp4").write_bytes(b"v")
    return out


def _series_scheduler(db, pubs, now, sleeps):
    cfg = load_settings({})

    async def fake_sleep(s):
        sleeps.append(s)

    return Scheduler(db, cfg, pubs, Media(), Notifier(), RuntimeSettings(db, cfg),
                     now_fn=lambda: now, sleep=fake_sleep)


async def test_series_published_last_to_first_as_one_post(db, tmp_path):
    cs = _series(db, tmp_path, [State.APPROVED] * 3)
    tk = Pub("tiktok")
    sleeps = []
    s = _series_scheduler(db, [tk], cs[0].created_at + timedelta(minutes=5), sleeps)
    assert await s.tick()
    assert [j.caption.split("\n")[0] for j in tk.jobs] == ["(3/3) t", "(2/3) t", "(1/3) t"]
    assert sleeps == [120, 120]
    assert all(db.get(c.id).state is State.PUBLISHED for c in cs)
    assert db.publications_count_since(cs[0].created_at) == 1


async def test_series_waits_for_all_parts_approved(db, tmp_path):
    cs = _series(db, tmp_path, [State.APPROVED, State.AWAITING_APPROVAL, State.APPROVED])
    tk = Pub("tiktok")
    s = _series_scheduler(db, [tk], cs[0].created_at + timedelta(minutes=5), [])
    assert not await s.tick()
    assert tk.jobs == []


async def test_series_total_failure_stops_series_and_backs_off(db, tmp_path):
    cs = _series(db, tmp_path, [State.APPROVED] * 3)
    ok = {"v": False}

    class Flaky(Pub):
        async def publish(self, job):
            if not ok["v"] and job.caption.startswith("(3/3)"):
                self.jobs.append(job)
                raise PublishError("caiu")
            return await super().publish(job)

    tk = Flaky("tiktok")
    clock = {"now": cs[0].created_at + timedelta(minutes=5)}
    cfg = load_settings({})

    async def nosleep(_s):
        pass

    s = Scheduler(db, cfg, [tk], Media(), Notifier(), RuntimeSettings(db, cfg),
                  now_fn=lambda: clock["now"], sleep=nosleep)
    await s.tick()
    assert len(tk.jobs) == 1  # parte 2 e 1 NAO saem
    assert all(db.get(c.id).state is State.APPROVED for c in cs)
    assert s.retry_at is not None and len(s.notifier.alerts) == 1
    ok["v"] = True
    clock["now"] = s.retry_at + timedelta(seconds=1)
    assert await s.tick()
    assert [j.caption.split("\n")[0] for j in tk.jobs[1:]] == ["(3/3) t", "(2/3) t", "(1/3) t"]
    assert s.retry_at is None


async def test_partly_published_series_ignores_interval_and_daily_limit(db, tmp_path):
    cs = _series(db, tmp_path, [State.APPROVED, State.APPROVED, State.PUBLISHED])
    now = cs[0].created_at + timedelta(minutes=5)
    db.record_publication(cs[2].id, "tiktok", "x", "u", now)
    tk = Pub("tiktok")
    cfg = load_settings({})
    rt = RuntimeSettings(db, cfg)
    s = Scheduler(db, cfg, [tk], Media(), Notifier(), rt, now_fn=lambda: now,
                  sleep=lambda _s: _noop())
    # limite diario ja atingido e intervalo (45 min) nao passou
    for _ in range(rt.post_max_per_day()):
        other = make_candidate(clip_id=f"o{_}", channel="x", state=State.PUBLISHED)
        db.insert_candidate(other)
        db.record_publication(other.id, "tiktok", "y", "u", now)
    assert await s.tick()
    assert [j.caption.split("\n")[0] for j in tk.jobs] == ["(2/3) t", "(1/3) t"]


async def _noop():
    pass


async def test_series_uses_fresh_clock_and_skips_discarded_part(db, tmp_path):
    cs = _series(db, tmp_path, [State.APPROVED] * 3)
    base = cs[0].created_at + timedelta(minutes=5)
    clock = {"now": base}
    cfg = load_settings({})

    async def sleep(sec):
        clock["now"] += timedelta(seconds=sec)
        if sec and not clock.get("done"):
            clock["done"] = True
            c = db.get(cs[1].id)
            c.state = State.DISCARDED
            db.update(c)

    tk = Pub("tiktok")
    s = Scheduler(db, cfg, [tk], Media(), Notifier(), RuntimeSettings(db, cfg),
                  now_fn=lambda: clock["now"], sleep=sleep)
    await s.tick()
    assert [j.caption.split("\n")[0] for j in tk.jobs] == ["(3/3) t", "(1/3) t"]
    assert db.get(cs[1].id).state is State.DISCARDED
    times = sorted(r["published_at"] for r in db.conn.execute("SELECT published_at FROM publications"))
    assert times[0] != times[1]
