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

    async def render(src, dst, credit, subs, title, facecam):
        calls.append((src, dst, credit, subs, title, facecam))
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
    async def broken(*a):
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
