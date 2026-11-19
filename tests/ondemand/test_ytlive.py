import json

import httpx
import pytest
import respx

from liveclips.ondemand.ytlive import (download_live_audio, fetch_segments, pick_formats,
                                       segment_range)
from liveclips.processing.ffmpeg import MediaError

DASH = "http_dash_segments_generator"


def fmt(itag, vcodec="none", abr=None, height=None, url=None):
    return {"format_id": str(itag), "vcodec": vcodec, "abr": abr, "height": height,
            "protocol": DASH, "url": url or f"https://gv.example/{itag}?x=1",
            "http_headers": {"User-Agent": "t"}, "target_duration": 5}


INFO = {"formats": [fmt(139, abr=48), fmt(140, abr=130), fmt(137, "avc1.640028", height=1080),
                    fmt(136, "avc1.4d401f", height=720), fmt(248, "vp9", height=1080),
                    fmt(299, "avc1.64002a", height=1440)]}


def test_segment_range():
    assert segment_range(0, 30, 5) == range(0, 6)
    assert segment_range(12, 31, 5) == range(2, 7)


def test_pick_formats():
    a, v = pick_formats(INFO)
    assert a["format_id"] == "140" and v["format_id"] == "137"
    with pytest.raises(MediaError):
        pick_formats({"formats": [fmt(137, "avc1.640028", height=1080)]})
    with pytest.raises(MediaError):
        pick_formats({"formats": [fmt(140, abr=130)]})


@respx.mock
async def test_fetch_segments_in_order_and_retry(tmp_path):
    f = fmt(140)
    base = f["url"]
    respx.get(base + "&sq=0").mock(return_value=httpx.Response(200, content=b"AA"))
    respx.get(base + "&sq=1").mock(side_effect=[httpx.Response(500), httpx.Response(200, content=b"BB")])
    respx.get(base + "&sq=2").mock(return_value=httpx.Response(200, content=b"CC"))
    async with httpx.AsyncClient() as http:
        out = await fetch_segments(http, f, range(0, 3), tmp_path / "a.m4a", concurrency=3,
                                   retry_wait=0)
    assert out.read_bytes() == b"AABBCC"


@respx.mock
async def test_fetch_segments_ignores_404_on_last_only(tmp_path):
    f = fmt(140)
    respx.get(f["url"] + "&sq=0").mock(return_value=httpx.Response(200, content=b"AA"))
    respx.get(f["url"] + "&sq=1").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as http:
        out = await fetch_segments(http, f, range(0, 2), tmp_path / "a.m4a", retry_wait=0)
        assert out.read_bytes() == b"AA"
        respx.get(f["url"] + "&sq=2").mock(return_value=httpx.Response(200, content=b"CC"))
        with pytest.raises(MediaError):
            await fetch_segments(http, f, range(0, 3), tmp_path / "b.m4a", retry_wait=0)


@respx.mock
async def test_download_live_audio_command(tmp_path):
    cmds = []

    async def run(*cmd, timeout=300):
        cmds.append(cmd)
        if cmd[0] == "yt-dlp":
            return json.dumps(INFO)
        cmd_out = tmp_path / "a.mp3"
        cmd_out.write_bytes(b"m")
        return ""

    a = [f for f in INFO["formats"] if f["format_id"] == "140"][0]
    respx.get(url__startswith=a["url"]).mock(return_value=httpx.Response(200, content=b"zz"))
    async with httpx.AsyncClient() as http:
        out = await download_live_audio("u", tmp_path / "a.mp3", 12, 31, http, run=run)
    assert out == tmp_path / "a.mp3"
    ff = next(c for c in cmds if c[0] == "ffmpeg")
    assert ff[ff.index("-ss") + 1] == "2.000"  # 12 - 2*5
    assert ff[ff.index("-t") + 1] == "19.000"
    assert not list(tmp_path.glob("*.m4a"))
