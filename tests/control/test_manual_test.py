import asyncio
from pathlib import Path

from liveclips.ai.verdict import Verdict
from liveclips.capture.twitch.api import TwitchError
from liveclips.capture.twitch.chat import ChannelInfo
from liveclips.control.manual_test import ManualTester
from liveclips.control.monitor import Monitor
from liveclips.core.config import load_settings
from liveclips.core.models import State

CLIP = {"id": "c1", "url": "https://clips.twitch.tv/c1", "broadcaster_name": "Loud",
        "created_at": "2026-11-19T03:00:00Z", "duration": 30, "title": "t", "view_count": 0}


async def nosleep(_):
    return None


class FakeAPI:
    def __init__(self, error=None, ready=True):
        self.error = error
        self.ready = ready
        self.created_for = []

    async def create_clip(self, broadcaster_id):
        self.created_for.append(broadcaster_id)
        if self.error:
            raise self.error
        return "c1"

    async def get_clip(self, clip_id):
        return CLIP if self.ready else None

    async def get_user(self, login):
        return {"login": login, "id": "777"} if login == "existe" else None


class FakeChat:
    def __init__(self, active=()):
        self.channels = {"gaules": ChannelInfo("gaules", "1", 9000),
                         "loud": ChannelInfo("loud", "2", 500)}
        self.active = list(active)

    def most_active(self):
        return self.active


class FakePipeline:
    """Imita o pipeline real: grava vídeo e veredito no candidato."""

    def __init__(self, db, tmp_path, score=8.0, safe=True, state=State.QUEUED):
        self.db, self.tmp, self.score, self.safe, self.state = db, tmp_path, score, safe, state
        self.processed = []

    def min_score(self):
        return 7.0

    async def process(self, c):
        self.processed.append(c.id)
        assert self.db.get(c.id).state is State.PROCESSING  # nunca disponível p/ o pipeline normal
        v = self.tmp / "vertical.mp4"
        v.write_bytes(b"v")
        c.vertical_path = str(v)
        c.ai_score = self.score
        c.ai_json = Verdict(score=self.score, safe=self.safe, reason="caos total",
                            title="Explodiu", caption="olha isso", hashtags=["#GTA6"]).model_dump_json()
        c.state = self.state
        self.db.update(c)


async def fake_preview(src, dst):
    dst.write_bytes(b"p")
    return dst


def tester(db, tmp_path, api=None, chat=None, **pipe):
    return ManualTester(api or FakeAPI(), db, FakePipeline(db, tmp_path, **pipe),
                        chat or FakeChat(), fake_preview, nosleep)


async def test_uses_most_active_channel_and_never_queues(db, tmp_path):
    api = FakeAPI()
    t = tester(db, tmp_path, api=api, chat=FakeChat(active=["loud"]))
    r = await t.run()
    assert api.created_for == ["2"]  # loud é o mais ativo no chat
    [c] = [db.get(1)]
    assert c.state is State.TESTED and c.source == "manual_test"
    assert "SERIA PUBLICADO" in r.text and "Nota da IA: **8.0**" in r.text
    assert "twitch.tv/loud" in r.text and r.preview_path.name == "preview.mp4"
    assert db.by_state(State.QUEUED) == []


async def test_falls_back_to_biggest_live_without_chat_activity(db, tmp_path):
    api = FakeAPI()
    await tester(db, tmp_path, api=api).run()
    assert api.created_for == ["1"]  # gaules tem mais espectadores


async def test_rejected_clip_still_shows_preview_and_reason(db, tmp_path):
    r = await tester(db, tmp_path, score=3.0, state=State.REJECTED_AI).run()
    assert "seria rejeitado" in r.text and "caos total" in r.text
    assert r.preview_path is not None


async def test_named_channel_outside_monitor_is_looked_up(db, tmp_path):
    api = FakeAPI()
    await tester(db, tmp_path, api=api).run("#Existe")
    assert api.created_for == ["777"]
    r = await tester(db, tmp_path).run("ninguem")
    assert "Canal não encontrado" in r.text and r.preview_path is None


async def test_create_clip_error_is_explained(db, tmp_path):
    r = await tester(db, tmp_path, api=FakeAPI(error=TwitchError(403, "x"))).run()
    assert "não permite clipes" in r.text and r.preview_path is None


async def test_clip_not_ready(db, tmp_path):
    r = await tester(db, tmp_path, api=FakeAPI(ready=False)).run()
    assert "não o liberou" in r.text


async def test_only_one_test_at_a_time(db, tmp_path):
    t = tester(db, tmp_path)
    release = asyncio.Event()
    original = t.pipeline.process

    async def slow(c):
        await release.wait()
        await original(c)

    t.pipeline.process = slow
    first = asyncio.create_task(t.run())
    await asyncio.sleep(0)
    second = await t.run()
    assert "Já tem um teste rodando" in second.text
    release.set()
    await first


class FakeTransport:
    def __init__(self):
        self.sent, self.files = [], []

    async def send(self, text, buttons=None):
        self.sent.append(text)

    async def send_file(self, path, text):
        self.files.append((path, text))


async def test_monitor_run_test_sends_preview_through_outbox(db, tmp_path):
    transport = FakeTransport()
    m = Monitor(db, transport, {}, load_settings({}))
    assert "indisponível" in await m.run_test()
    m.tester = tester(db, tmp_path)
    reply = await m.run_test()
    assert "Teste concluído" in reply
    await m.flush()
    path, text = transport.files[0]
    assert path.name == "preview.mp4" and "TESTE (nada foi publicado)" in text
