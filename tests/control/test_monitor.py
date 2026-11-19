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
    m = Monitor(db, t, pubs or {}, load_settings({}), clock=clock)
    return m, t, clock


def test_pause_resume_and_status(db):
    m, _, _ = make_monitor(db)
    assert "pausadas" in m.handle_command("pause")
    assert db.kv_get("paused") == "1"
    assert "pausado" in m.handle_command("/status")
    status = m.handle_command("status")
    assert "Lives monitoradas" not in status and "bloqueados" not in status
    m.handle_command("resume")
    assert db.kv_get("paused") == "0"


def test_min_score_and_help(db):
    m, _, _ = make_monitor(db)
    assert "8.5" in m.handle_command("min_score", "8,5")
    assert db.kv_get("min_score") == "8.5"
    assert "entre 0 e 10" in m.handle_command("min_score", "11")
    assert "Uso" in m.handle_command("min_score", "abc")
    assert "Comandos" in m.handle_command("xyz")
    assert "/testar" not in m.handle_command("xyz") and "/block" not in m.handle_command("xyz")


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
    assert buttons == [("🗑️ Apagar post", f"del:{c.id}")]


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
    m._enqueue(("file", str(tmp_path / "p.mp4"), "leg"))
    await m.flush()
    assert "prévia não enviada" in t.sent[0][0]


async def test_outbox_drops_oldest_when_full(db):
    from liveclips.control import monitor as mon

    m, t, _ = make_monitor(db)
    for i in range(mon.OUTBOX_MAX + 5):
        await m.info(f"msg {i}")
    await m.flush()
    assert len(t.sent) == mon.OUTBOX_MAX and t.sent[0][0] == "msg 5"


def test_platform_command_turns_platforms_on_and_off(db):
    from liveclips.control.runtime import RuntimeSettings

    cfg = load_settings({"DRY_RUN": "1"})
    rt = RuntimeSettings(db, cfg)
    m = Monitor(db, FakeTransport(), {}, cfg, runtime=rt)
    assert "ligado" in m.handle_command("plataforma", "tiktok on")
    assert rt.platform_enabled("tiktok")
    assert "desligado" in m.handle_command("plataforma", "TikTok OFF")
    assert not rt.platform_enabled("tiktok")
    assert "Uso:" in m.handle_command("plataforma", "orkut on")
    status = m.status_text()
    assert "Plataformas: nenhuma ligada" in status and "ensaio" not in status
