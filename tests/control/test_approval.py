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

    async def edit(self, message_id, candidate_id, text, buttons, retry=()):
        self.edits.append((message_id, candidate_id, text, buttons, retry))


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


async def test_new_card_posted_even_with_many_pending_cards(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    for i in range(60):
        c = awaiting(db, clip_id=f"twitch:{i}")
        c.approval_message_id = 500 + i
        db.update(c)
    new = awaiting(db, clip_id="twitch:novo", rank=0)
    assert await desk.tick() == 1
    assert t.posts[0][0] == new.id


async def test_failed_platform_gets_retry_button(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    calls = []

    async def retry(c, platform):
        calls.append((c.id, platform))
        return "🔁 reenviado"

    desk.retry = retry
    c = awaiting(db)
    await desk.tick()
    await desk.handle("approve", c.id)
    got = db.get(c.id)
    db.record_publication(c.id, "instagram", "ig1", "https://ig/p/1", got.created_at)
    got.state, got.reason = State.PUBLISHED, "tiktok: token vencido"
    db.update(got)
    await desk.on_status(got)
    msg_id, cid, text, buttons, retry_btns = t.edits[-1]
    assert "TikTok ❌ token vencido" in text and retry_btns == ("tiktok",)
    assert desk.failed_platforms(got) == ["tiktok"]
    assert await desk.handle("retry-tiktok", c.id) == "🔁 reenviado"
    assert calls == [(c.id, "tiktok")]


async def test_retry_only_for_published_clips(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    c = awaiting(db)
    assert "não foi publicado" in await desk.handle("retry-tiktok", c.id)


def _published_with_failure(db, desk):
    c = awaiting(db)
    got = db.get(c.id)
    got.state, got.reason = State.PUBLISHED, "tiktok: token vencido"
    db.update(got)
    return c


async def test_retry_for_platform_not_failed_is_refused(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    calls = []

    async def retry(c, platform):
        calls.append(platform)
        return "ok"

    desk.retry = retry
    c = _published_with_failure(db, desk)
    assert "não está com falha" in await desk.handle("retry-instagram", c.id)
    assert calls == []


async def test_concurrent_retry_is_refused(db, tmp_path):
    import asyncio
    desk, t, _ = make_desk(db, tmp_path)
    gate, calls = asyncio.Event(), []

    async def retry(c, platform):
        calls.append(platform)
        await gate.wait()
        return "ok"

    desk.retry = retry
    c = _published_with_failure(db, desk)
    first = asyncio.create_task(desk.handle("retry-tiktok", c.id))
    await asyncio.sleep(0)
    second = await asyncio.wait_for(desk.handle("retry-tiktok", c.id), timeout=2)
    assert "já em andamento" in second
    gate.set()
    assert await first == "ok"
    assert calls == ["tiktok"]


async def test_ondemand_card_shows_video_and_time(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    awaiting(db, source="ondemand", title="Podpah #1049", start_s=5020.0, platform="youtube",
             channel="Podpah")
    await desk.tick()
    text = t.posts[0][1]
    assert "🔗 Podpah #1049 · 1:23:40" in text and "Automático" not in text


async def test_card_never_shows_age_warning(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    c = awaiting(db, title="Podpah #1049", start_s=60.0)
    desk.now_fn = lambda: c.created_at + timedelta(hours=5)
    await desk.tick()
    assert "⏰" not in t.posts[0][1] and "🔗 Podpah #1049 · " in t.posts[0][1]


# --- séries ---------------------------------------------------------------
RECAP = Verdict(score=7.5, safe=True, reason="r", title="PARTE 2", caption="leg2",
                hashtags=["#x"], series_recap="Até aqui: ele caiu")


def series(db, n=2, states=None):
    parts = []
    for i in range(1, n + 1):
        st = (states or {}).get(i, State.AWAITING_APPROVAL)
        v = V if i == 1 else RECAP
        c = make_candidate(state=st, ai_score=7.5, ai_json=v.model_dump_json(),
                           clip_id=f"s:{i}", series_id="ser1", part=i, parts=n)
        db.insert_candidate(c)
        parts.append(c)
    return parts


async def test_series_posts_one_card_and_parts_without_buttons(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    p1, p2 = series(db)
    assert await desk.tick() == 2
    assert await desk.tick() == 0
    assert [(p[0], p[4]) for p in t.posts] == [(p1.id, True), (p2.id, False)]
    assert "Série em 2 partes" in t.posts[0][1]
    assert t.posts[1][1].startswith("🎞️ Parte 2/2")
    assert db.get(p1.id).approval_message_id == 1001
    assert db.get(p2.id).approval_message_id == 1002


async def test_series_waits_for_all_parts(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    series(db, states={2: State.DETECTED})
    assert await desk.tick() == 0 and t.posts == []


async def test_series_render_failure_fails_whole_series(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path, render_ok=False)
    p1, p2 = series(db)
    assert await desk.tick() == 0 and t.posts == []
    assert db.get(p1.id).state is State.FAILED and db.get(p2.id).state is State.FAILED


async def test_series_approve_applies_to_all(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    p1, p2 = series(db)
    await desk.tick()
    await desk.handle("now", p1.id)
    assert db.get(p1.id).state is State.APPROVED and db.get(p2.id).state is State.APPROVED
    assert db.get(p2.id).priority == 1
    assert {e[1] for e in t.edits} == {p1.id, p2.id}


async def test_series_discard_applies_to_all(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    p1, p2 = series(db)
    await desk.tick()
    await desk.handle("discard", p2.id)
    assert db.get(p1.id).state is State.DISCARDED and db.get(p2.id).state is State.DISCARDED
    assert not (tmp_path / str(p1.id)).exists() and not (tmp_path / str(p2.id)).exists()


async def test_series_edit_resets_all_and_reposts(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    p1, p2 = series(db)
    await desk.tick()
    assert "Salvo" in await desk.edit(p1.id, "NOVO", "nova leg")
    g1, g2 = db.get(p1.id), db.get(p2.id)
    assert g1.approval_message_id is None and g2.approval_message_id is None
    assert g1.vertical_path is None and g2.vertical_path is None
    v1 = Verdict.model_validate_json(g1.ai_json)
    v2 = Verdict.model_validate_json(g2.ai_json)
    assert v1.title == "NOVO" and v2.title == "PARTE 2" and v2.series_recap == "Até aqui: ele caiu"
    assert v1.caption == v2.caption == "nova leg"
    assert sum("Editado" in e[2] for e in t.edits) == 2
    assert await desk.tick() == 2 and len(t.posts) == 4


async def test_series_status_line_tiktok_hint(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    p1, p2 = series(db)
    await desk.tick()
    for p in (p1, p2):
        db.record_publication(p.id, "tiktok", "tt", "", p.created_at)
        g = db.get(p.id)
        g.state = State.PUBLISHED
        db.update(g)
    assert "TikTok: publique as partes do app na ordem 2 → 1" in desk.status_line(db.get(p1.id))
    assert "publique as partes" not in desk.status_line(db.get(p2.id))


async def test_series_edit_survives_discord_failure(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    p1, p2 = series(db)
    await desk.tick()

    async def boom(*a, **k):
        raise RuntimeError("404 mensagem apagada")

    t.edit = boom
    await desk.edit(p1.id, "NOVO", "leg")
    assert db.get(p1.id).approval_message_id is None and db.get(p2.id).approval_message_id is None
    assert await desk.tick() == 2 and len(t.posts) == 4


async def test_series_orphan_part_is_reposted_alone_without_buttons(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    p1, p2 = series(db)
    fail = {"on": True}

    async def post(text, video, link, candidate_id, buttons):
        if candidate_id == p2.id and fail["on"]:
            raise RuntimeError("discord fora do ar")
        return await FakeTransport.post(t, text, video, link, candidate_id, buttons)

    t.post = post
    await desk.tick()
    assert db.get(p1.id).approval_message_id is not None
    assert db.get(p2.id).approval_message_id is None
    fail["on"] = False
    assert await desk.tick() == 1
    assert t.posts[-1][0] == p2.id and t.posts[-1][4] is False
    assert [p[0] for p in t.posts].count(p1.id) == 1


async def test_series_approve_from_part_2_approves_all(db, tmp_path):
    desk, t, _ = make_desk(db, tmp_path)
    p1, p2 = series(db)
    await desk.tick()
    await desk.handle("approve", p2.id)
    assert db.get(p1.id).state is State.APPROVED and db.get(p2.id).state is State.APPROVED
