from factories import make_candidate
from liveclips.core.config import load_settings
from liveclips.ai.verdict import Verdict
from liveclips.core.models import State
from liveclips.control.monitor import Monitor
from liveclips.publishing.platforms import PostResult

V = Verdict(score=8, safe=True, reason="caos", title="Explodiu", caption="c", hashtags=[])


class FakeTransport:
    def __init__(self):
        self.sent = []
        self.files = []

    async def send(self, text, buttons=None):
        self.sent.append((text, buttons))

    async def send_file(self, path, text):
        self.files.append((path, text))


class DeletablePub:
    name = "instagram"

    def __init__(self, ok=True):
        self.ok = ok
        self.deleted = []

    async def delete(self, post_id):
        self.deleted.append(post_id)
        return self.ok


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def make_monitor(db, pubs=None):
    t = FakeTransport()
    clock = Clock()
    m = Monitor(db, t, pubs or {}, load_settings({}), channel_count=lambda: 42, clock=clock,
                chat_summary=lambda: "chat: 200/200 canais, 120 msgs/min no total")
    return m, t, clock


def test_pause_resume_and_status(db):
    m, _, _ = make_monitor(db)
    assert "pausadas" in m.handle_command("pause")
    assert db.kv_get("paused") == "1"
    assert "pausado" in m.handle_command("/status") and "42" in m.handle_command("status")
    assert "120 msgs/min" in m.handle_command("status")
    m.handle_command("resume")
    assert db.kv_get("paused") == "0"


def test_block_unblock_and_min_score(db):
    m, _, _ = make_monitor(db)
    assert "Uso" in m.handle_command("block")
    m.handle_command("block", "Gaules")
    assert db.is_blocked("gaules")
    m.handle_command("unblock", "gaules")
    assert not db.is_blocked("gaules")
    assert "8.5" in m.handle_command("min_score", "8,5")
    assert db.kv_get("min_score") == "8.5"
    assert "entre 0 e 10" in m.handle_command("min_score", "11")
    assert "Uso" in m.handle_command("min_score", "abc")
    assert "Comandos" in m.handle_command("xyz")


async def test_delete_action_uses_publisher_and_marks_deleted(db):
    pub = DeletablePub()
    m, _, _ = make_monitor(db, {"instagram": pub})
    c = make_candidate(state=State.PUBLISHED)
    db.insert_candidate(c)
    db.record_publication(c.id, "instagram", "m1", "https://ig/m1", c.created_at)
    db.record_publication(c.id, "tiktok", "p1", "", c.created_at)
    msg = await m.handle_action(f"del:{c.id}")
    assert pub.deleted == ["m1"]
    assert "instagram: apagado" in msg and "tiktok: apague manualmente" in msg
    assert db.get(c.id).state is State.DELETED


async def test_block_action(db):
    m, _, _ = make_monitor(db)
    assert "bloqueado" in await m.handle_action("block:loud")
    assert db.is_blocked("loud")


async def test_alert_dedups_for_10_minutes(db):
    m, t, clock = make_monitor(db)
    await m.alert("token caiu")
    await m.alert("token caiu")
    clock.t = 601
    await m.alert("token caiu")
    await m.flush()
    assert len(t.sent) == 2


async def test_post_published_has_buttons(db):
    m, t, _ = make_monitor(db)
    c = make_candidate(ai_score=8.0)
    db.insert_candidate(c)
    await m.post_published(c, V, [PostResult("instagram", "m1", "https://ig/m1")], [])
    await m.flush()
    text, buttons = t.sent[0]
    assert "twitch.tv/gaules" in text and "https://ig/m1" in text
    assert buttons == [("🗑️ Apagar post", f"del:{c.id}"), ("🚫 Bloquear canal", "block:gaules")]


async def test_dry_run_preview_sends_file(db, tmp_path):
    m, t, _ = make_monitor(db)
    c = make_candidate(ai_score=7.5)
    await m.dry_run_preview(c, V, "legenda final", tmp_path / "p.mp4")
    await m.flush()
    path, text = t.files[0]
    assert "ENSAIO" in text and "legenda final" in text


async def test_sending_never_blocks_the_caller(db):
    import asyncio

    class HangingTransport(FakeTransport):
        async def send(self, text, buttons=None):
            await asyncio.sleep(3600)  # Discord fora do ar

    m = Monitor(db, HangingTransport(), {}, load_settings({}))
    await asyncio.wait_for(m.alert("x"), timeout=0.5)  # só enfileira
    await asyncio.wait_for(m.info("y"), timeout=0.5)


async def test_file_failure_falls_back_to_text(db, tmp_path):
    class NoFiles(FakeTransport):
        async def send_file(self, path, text):
            raise RuntimeError("arquivo grande demais")

    t = NoFiles()
    m = Monitor(db, t, {}, load_settings({}))
    await m.dry_run_preview(make_candidate(ai_score=7.0), V, "leg", tmp_path / "p.mp4")
    await m.flush()
    assert "prévia não enviada" in t.sent[0][0]


async def test_outbox_drops_oldest_when_full(db):
    from liveclips.control import monitor as mon

    m, t, _ = make_monitor(db)
    for i in range(mon.OUTBOX_MAX + 5):
        await m.info(f"msg {i}")
    await m.flush()
    assert len(t.sent) == mon.OUTBOX_MAX and t.sent[0][0] == "msg 5"
