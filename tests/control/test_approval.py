from datetime import timedelta
from pathlib import Path

from factories import make_candidate
from liveclips.ai.verdict import Verdict
from liveclips.control.approval import ApprovalDesk, approval_custom_id, parse_custom_id
from liveclips.control.runtime import RuntimeSettings
from liveclips.core.config import load_settings
from liveclips.core.models import State

V = Verdict(score=7.5, safe=True, reason="reação absurda", title="ELE CAIU?",
            caption="olha isso", hashtags=["#twitch"])


class FakeTransport:
    def __init__(self):
        self.posts = []
        self.edits = []

    async def post(self, text, video, link, candidate_id, buttons):
        self.posts.append((candidate_id, text, video, link, buttons))
        return 1000 + len(self.posts)

    async def edit(self, message_id, candidate_id, text, buttons):
        self.edits.append((message_id, candidate_id, text, buttons))


def make_desk(db, tmp_path, size=10, limit_mb=10.0, render_ok=True, shrink_size=5, **env):
    cfg = load_settings(env)
    rt = RuntimeSettings(db, cfg)
    t = FakeTransport()
    renders = []

    async def render(c, verdict):
        renders.append(verdict.title)
        if not render_ok:
            c.state = State.FAILED
            db.update(c)
            return False
        final = tmp_path / str(c.id) / "final.mp4"
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(b"x" * size)
        c.vertical_path = str(final)
        db.update(c)
        return True

    async def shrink(src, dst):
        dst.write_bytes(b"s" * shrink_size)
        return dst

    async def expose(path):
        return "https://tunel.example/media/final.mp4"

    desk = ApprovalDesk(db, rt, t, render, shrink, expose, ["instagram", "tiktok"],
                        upload_limit_mb=limit_mb)
    desk.renders = renders
    return desk, t, rt


def awaiting(db, **kw):
    c = make_candidate(state=State.AWAITING_APPROVAL, ai_score=7.5, ai_json=V.model_dump_json(),
                       **kw)
    db.insert_candidate(c)
    return c


def test_custom_ids_roundtrip():
    assert parse_custom_id(approval_custom_id("now", 42)) == ("now", 42)
    assert parse_custom_id("del:42") is None
    assert parse_custom_id("appr:explode:42") is None


async def test_tick_posts_card_once_with_video_and_buttons(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    c = awaiting(db)
    assert await desk.tick() == 1
    assert await desk.tick() == 0  # não posta de novo
    cid, text, video, link, buttons = t.posts[0]
    assert cid == c.id and buttons and link is None and video.name == "final.mp4"
    assert "ELE CAIU?" in text and "7.5" in text and "reação absurda" in text
    assert "Reels" in text and "TikTok" in text
    assert db.get(c.id).approval_message_id == 1001


async def test_big_video_is_shrunk_then_linked(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path, size=3 * 1024 * 1024, limit_mb=1.0,
                           shrink_size=2 * 1024 * 1024)
    awaiting(db)
    await desk.tick()
    _, _, video, link, _ = t.posts[0]
    assert video is None and link == "https://tunel.example/media/final.mp4"


async def test_shrunk_video_is_attached_when_it_fits(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path, size=3 * 1024 * 1024, limit_mb=1.0, shrink_size=10)
    awaiting(db)
    await desk.tick()
    assert t.posts[0][2].name == "discord.mp4"


async def test_render_failure_posts_nothing(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path, render_ok=False)
    c = awaiting(db)
    assert await desk.tick() == 0 and t.posts == []
    assert db.get(c.id).state is State.FAILED


async def test_approve_and_now_and_discard(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    a, b, d = awaiting(db, clip_id="a"), awaiting(db, clip_id="b"), awaiting(db, clip_id="d")
    await desk.tick()
    assert "fila" in await desk.handle("approve", a.id)
    assert db.get(a.id).state is State.APPROVED and db.get(a.id).priority == 0
    assert "agora" in (await desk.handle("now", b.id)).lower()
    assert db.get(b.id).priority == 1
    assert "Descartado" in await desk.handle("discard", d.id)
    assert db.get(d.id).state is State.DISCARDED
    assert not (tmp_path / str(d.id)).exists()  # arquivos apagados
    assert "já foi decidido" in await desk.handle("approve", a.id)
    # o card some com os botões depois da decisão
    assert any(e[1] == a.id and e[3] is False and "aprovado" in e[2] for e in t.edits)


async def test_edit_rerenders_with_new_title(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    c = awaiting(db)
    await desk.tick()
    msg = await desk.edit(c.id, "  CAIU DE NOVO?  ", "nova legenda")
    assert "Salvo" in msg
    got = db.get(c.id)
    assert Verdict.model_validate_json(got.ai_json).title == "CAIU DE NOVO?"
    assert got.approval_message_id is None and got.vertical_path is None
    assert t.edits[-1][3] is False and "Editado" in t.edits[-1][2]
    await desk.tick()  # novo card com o vídeo refeito
    assert desk.renders[-1] == "CAIU DE NOVO?" and len(t.posts) == 2
    assert "O título não pode ficar vazio." == await desk.edit(c.id, "   ", "x")


async def test_status_after_publication_lists_platforms(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    c = awaiting(db)
    await desk.tick()
    await desk.handle("approve", c.id)
    got = db.get(c.id)
    db.record_publication(c.id, "instagram", "ig1", "https://ig/p/1", got.created_at)
    db.record_publication(c.id, "tiktok", "tt1", "", got.created_at)
    got.state = State.PUBLISHED
    db.update(got)
    await desk.on_status(got)
    text = t.edits[-1][2]
    assert "Reels ✅ https://ig/p/1" in text and "TikTok 📥 rascunho" in text


async def test_old_automatic_card_shows_age(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    c = awaiting(db)
    await desk.tick()
    desk.now_fn = lambda: c.created_at + timedelta(hours=3, minutes=5)
    await desk.tick()
    assert "⏰ há 3 h" in t.edits[-1][2]
    await desk.tick()
    assert len(t.edits) == 1  # só edita quando a hora muda


async def test_one_broken_card_does_not_block_the_others(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    broken = set()

    async def post(text, video, link, candidate_id, buttons):
        if candidate_id in broken:
            raise RuntimeError("discord fora do ar")
        return await FakeTransport.post(t, text, video, link, candidate_id, buttons)

    t.post = post
    bad, good = awaiting(db, clip_id="bad"), awaiting(db, clip_id="good")
    broken.add(bad.id)
    assert await desk.tick() == 1
    assert db.get(good.id).approval_message_id == 1001
    assert db.get(bad.id).approval_message_id is None
    assert db.get(bad.id).state is State.AWAITING_APPROVAL


async def test_tick_ignores_manual_test_clips(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    awaiting(db, source="manual_test")
    assert await desk.tick() == 0 and t.posts == []


async def test_new_card_posted_even_with_many_pending_cards(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    for i in range(60):
        c = awaiting(db, clip_id=f"twitch:{i}")
        c.approval_message_id = 500 + i
        db.update(c)
    new = awaiting(db, clip_id="twitch:novo", rank=0)
    assert await desk.tick() == 1
    assert t.posts[0][0] == new.id
