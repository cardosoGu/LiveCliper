from pathlib import Path

import httpx
import pytest
import respx
from aiohttp.test_utils import TestClient, TestServer

from liveclips.core.config import load_settings
from liveclips.ai.verdict import Verdict
from liveclips.publishing.media_server import MediaServer
from liveclips.publishing.caption import build_caption
from liveclips.publishing.platforms import (InstagramPublisher, PublishError, PublishJob,
                                            TikTokPublisher, build_publishers)
from liveclips.publishing.platforms.tiktok import TT

V = Verdict(score=8, safe=True, reason="r", title="Explodiu tudo", caption="olha isso 😂",
            hashtags=["#GTA6", "#gtarp"])
IG = "https://graph.facebook.com/v23.0"


async def nosleep(_):
    return None


def job(tmp_path: Path) -> PublishJob:
    p = tmp_path / "v.mp4"
    p.write_bytes(b"0123456789")
    return PublishJob(p, "https://t.example/media/x.mp4", "legenda", "titulo")


def test_caption_has_credits_and_truncates_body_only():
    text = build_caption(V, "twitch", "gaules")
    assert "🎥 Créditos: twitch.tv/gaules" in text and text.endswith("#GTA6 #gtarp")
    long = Verdict(**{**V.model_dump(), "caption": "x" * 5000})
    cut = build_caption(long, "kick", "abc", max_len=300)
    assert len(cut) <= 300 and "kick.com/abc" in cut


@respx.mock
async def test_instagram_happy_path(tmp_path):
    respx.post(f"{IG}/42/media").mock(return_value=httpx.Response(200, json={"id": "c1"}))
    respx.get(f"{IG}/c1").mock(side_effect=[
        httpx.Response(200, json={"status_code": "IN_PROGRESS"}),
        httpx.Response(200, json={"status_code": "FINISHED"}),
    ])
    respx.post(f"{IG}/42/media_publish").mock(return_value=httpx.Response(200, json={"id": "m1"}))
    respx.get(f"{IG}/m1").mock(return_value=httpx.Response(
        200, json={"permalink": "https://instagram.com/reel/m1"}))
    async with httpx.AsyncClient() as http:
        r = await InstagramPublisher("42", "tok", http, sleep=nosleep).publish(job(tmp_path))
    assert (r.platform, r.post_id, r.url) == ("instagram", "m1", "https://instagram.com/reel/m1")


@respx.mock
async def test_instagram_container_error(tmp_path):
    respx.post(f"{IG}/42/media").mock(return_value=httpx.Response(200, json={"id": "c1"}))
    respx.get(f"{IG}/c1").mock(return_value=httpx.Response(
        200, json={"status_code": "ERROR", "status": "formato"}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(PublishError):
            await InstagramPublisher("42", "tok", http, sleep=nosleep).publish(job(tmp_path))


@respx.mock
async def test_tiktok_flow_sends_privacy_and_rotates_token(tmp_path):
    respx.post(f"{TT}/oauth/token/").mock(return_value=httpx.Response(
        200, json={"access_token": "a", "refresh_token": "r2"}))
    init = respx.post(f"{TT}/post/publish/video/init/").mock(return_value=httpx.Response(
        200, json={"data": {"upload_url": "https://up.example/u", "publish_id": "p1"},
                   "error": {"code": "ok"}}))
    up = respx.put("https://up.example/u").mock(return_value=httpx.Response(201))
    respx.post(f"{TT}/post/publish/status/fetch/").mock(return_value=httpx.Response(
        200, json={"data": {"status": "PUBLISH_COMPLETE"}}))
    rotated = []
    async with httpx.AsyncClient() as http:
        pub = TikTokPublisher("k", "s", "r1", "SELF_ONLY", http, rotated.append, nosleep)
        r = await pub.publish(job(tmp_path))
    assert r.post_id == "p1" and rotated == ["r2"]
    import json
    body = json.loads(init.calls[0].request.content)
    assert body["post_info"]["privacy_level"] == "SELF_ONLY"
    assert body["source_info"]["video_size"] == 10
    assert up.calls[0].request.headers["Content-Range"] == "bytes 0-9/10"


def test_build_publishers_respects_modes(db):
    cfg = load_settings({"IG_USER_ID": "1", "IG_ACCESS_TOKEN": "t",
                         "TIKTOK_MODE": "off", "TIKTOK_CLIENT_KEY": "k",
                         "YT_MODE": "private", "YT_CLIENT_ID": "c"})
    assert [p.name for p in build_publishers(cfg, db, None)] == ["instagram", "youtube"]


@respx.mock
async def test_media_server_public_base_resolution():
    respx.get("http://cloudflared:2000/quicktunnel").mock(
        return_value=httpx.Response(200, json={"hostname": "abc.trycloudflare.com"}))
    async with httpx.AsyncClient() as http:
        fixed = MediaServer(load_settings({"PUBLIC_BASE_URL": "https://fixo.example"}), http)
        railway = MediaServer(load_settings({"RAILWAY_PUBLIC_DOMAIN": "app.up.railway.app"}), http)
        tunnel = MediaServer(load_settings({}), http)
        assert await fixed.public_base() == "https://fixo.example"
        assert await railway.public_base() == "https://app.up.railway.app"
        assert await tunnel.public_base() == "https://abc.trycloudflare.com"


async def test_media_server_serves_and_expires(tmp_path):
    clock = [1000.0]
    srv = MediaServer(load_settings({"PUBLIC_BASE_URL": "https://x"}), None, lambda: clock[0])
    f = tmp_path / "v.mp4"
    f.write_bytes(b"video")
    url = await srv.expose(f, ttl_s=60)
    path = url.removeprefix("https://x")
    async with TestClient(TestServer(srv.app)) as client:
        assert (await (await client.get("/health")).text()) == "ok"
        resp = await client.get(path)
        assert resp.status == 200 and await resp.read() == b"video"
        clock[0] += 61
        assert (await client.get(path)).status == 404


@respx.mock
async def test_tiktok_draft_goes_to_inbox(tmp_path):
    respx.post(f"{TT}/oauth/token/").mock(return_value=httpx.Response(
        200, json={"access_token": "a", "refresh_token": "r1"}))
    inbox = respx.post(f"{TT}/post/publish/inbox/video/init/").mock(
        return_value=httpx.Response(200, json={
            "data": {"upload_url": "https://up.example/u", "publish_id": "p9"},
            "error": {"code": "ok"}}))
    direct = respx.post(f"{TT}/post/publish/video/init/")
    respx.put("https://up.example/u").mock(return_value=httpx.Response(201))
    respx.post(f"{TT}/post/publish/status/fetch/").mock(return_value=httpx.Response(
        200, json={"data": {"status": "SEND_TO_USER_INBOX"}}))
    async with httpx.AsyncClient() as http:
        pub = TikTokPublisher("k", "s", "r1", "SELF_ONLY", http, None, nosleep, draft=True)
        r = await pub.publish(job(tmp_path))
    import json
    body = json.loads(inbox.calls[0].request.content)
    assert r.post_id == "p9" and "post_info" not in body
    assert body["source_info"]["source"] == "FILE_UPLOAD"
    assert not direct.called


def test_build_publishers_tiktok_draft(db):
    cfg = load_settings({"TIKTOK_MODE": "draft", "TIKTOK_CLIENT_KEY": "k"})
    [tt] = build_publishers(cfg, db, None)
    assert tt.name == "tiktok" and tt.draft is True


def test_build_caption_series_part():
    from liveclips.ai.verdict import Verdict
    from liveclips.publishing.caption import build_caption
    v = Verdict(score=8, safe=True, reason="r", title="Titulo", caption="c", hashtags=["#a"])
    text = build_caption(v, "twitch", "gaules", part=2, parts=3)
    assert text.startswith("(2/3) Titulo")
    assert "Veja todas as partes no perfil." in text
    assert "Veja todas" not in build_caption(v, "twitch", "gaules")
