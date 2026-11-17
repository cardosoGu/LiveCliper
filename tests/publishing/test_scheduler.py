from datetime import timedelta
from pathlib import Path

from factories import make_candidate
from liveclips.core.config import load_settings
from liveclips.ai.verdict import Verdict
from liveclips.core.models import State
from liveclips.publishing.platforms import PostResult, PublishError
from liveclips.publishing.scheduler import Scheduler

V = Verdict(score=8, safe=True, reason="r", title="t", caption="c", hashtags=["#GTA6"])


class Pub:
    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail
        self.jobs = []

    async def publish(self, job):
        self.jobs.append(job)
        if self.fail:
            raise PublishError("caiu")
        return PostResult(self.name, f"{self.name}-id", f"https://{self.name}/x")


class Media:
    async def expose(self, path):
        return "https://t.example/media/x.mp4"


class Notifier:
    def __init__(self):
        self.published = []
        self.previews = []

    async def post_published(self, c, verdict, results, errors):
        self.published.append((c.id, [r.platform for r in results], errors))

    async def dry_run_preview(self, c, verdict, caption, preview_path):
        self.previews.append((c.id, caption))


async def fake_preview(src, dst):
    dst.write_bytes(b"p")
    return dst


def setup(db, tmp_path: Path, pubs, **env):
    cfg = load_settings(env)
    c = make_candidate(state=State.QUEUED, ai_score=8.0, ai_json=V.model_dump_json(),
                       vertical_path=str(tmp_path / "vertical.mp4"))
    (tmp_path / "vertical.mp4").write_bytes(b"v")
    db.insert_candidate(c)
    now = c.created_at + timedelta(minutes=5)
    notifier = Notifier()
    s = Scheduler(db, cfg, pubs, Media(), notifier, now_fn=lambda: now, preview_fn=fake_preview)
    return s, c, notifier, now


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
    assert db.get(c.id).state is State.QUEUED


async def test_interval_blocks_second_post(db, tmp_path):
    s, c, n, now = setup(db, tmp_path, [Pub("instagram")])
    await s.tick()
    other = make_candidate(clip_id="b", channel="outro", state=State.QUEUED, ai_score=9.0,
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


async def test_all_failing_marks_failed(db, tmp_path):
    s, c, n, now = setup(db, tmp_path, [Pub("instagram", fail=True)])
    await s.tick()
    got = db.get(c.id)
    assert got.state is State.FAILED and "caiu" in got.reason


async def test_dry_run_never_calls_publishers(db, tmp_path):
    ig = Pub("instagram")
    s, c, n, now = setup(db, tmp_path, [ig], DRY_RUN="1")
    await s.tick()
    assert ig.jobs == []
    assert n.previews and n.previews[0][0] == c.id
    assert db.publications_for(c.id)[0]["platform"] == "dry_run"


async def test_expired_queue_item(db, tmp_path):
    s, c, n, now = setup(db, tmp_path, [Pub("instagram")])
    late = c.created_at + timedelta(minutes=120)
    s.now_fn = lambda: late
    assert not await s.tick()
    assert db.get(c.id).state is State.EXPIRED
