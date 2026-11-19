import json
from pathlib import Path

from liveclips.ondemand.ingest import (download_audio, download_section, parse_live_chat,
                                       parse_metadata, probe)


def info(**kw):
    base = {"id": "abc", "title": "Podpah #1", "channel": "Podpah", "duration": 9659.0,
            "extractor_key": "Youtube", "is_live": False,
            "heatmap": [{"start_time": 0, "end_time": 10, "value": 0.2}]}
    base.update(kw)
    return base


def test_parse_metadata_detects_platform():
    m = parse_metadata(info(), "https://youtu.be/abc")
    assert (m.platform, m.title, m.channel, m.duration, m.is_live) == \
        ("youtube", "Podpah #1", "Podpah", 9659.0, False)
    assert m.heatmap and m.video_id == "abc"
    assert parse_metadata(info(extractor_key="TwitchVod", heatmap=None), "u").platform == "twitch"
    assert parse_metadata(info(extractor_key="Kick"), "u").platform == "kick"
    assert parse_metadata(info(channel=None, uploader="Fulano"), "u").channel == "Fulano"


def test_parse_metadata_credit_hint():
    yt = parse_metadata(info(channel="Inteligência Ltda", uploader_id="@Podpah"), "u")
    assert yt.channel == "Inteligência Ltda" and yt.credit_hint == "Podpah"
    uc = parse_metadata(info(uploader_id=None,
                             channel_url="https://www.youtube.com/channel/UCabc_123"), "u")
    assert uc.credit_hint == "channel/UCabc_123"
    handle_url = parse_metadata(info(uploader_id=None,
                                     channel_url="https://www.youtube.com/@Flow"), "u")
    assert handle_url.credit_hint == "Flow"
    assert parse_metadata(info(uploader_id=None, channel_url=None), "u").credit_hint == ""
    tw = parse_metadata(info(extractor_key="TwitchVod", uploader_id="gaules"), "u")
    assert tw.credit_hint == "gaules"
    out = parse_metadata(info(extractor_key="Vimeo"), "https://www.vimeo.com/123")
    assert (out.platform, out.credit_hint) == ("outro", "vimeo.com")


async def test_probe_and_downloads_build_yt_dlp_commands(tmp_path):
    calls = []

    async def run(*cmd, timeout=300):
        calls.append(cmd)
        if "-J" in cmd:
            return json.dumps(info())
        out = Path(cmd[cmd.index("-o") + 1].replace("%(ext)s", "mp3"))
        out.write_bytes(b"x")
        return ""

    m = await probe("https://youtu.be/abc", run=run)
    assert m.title == "Podpah #1"
    audio = await download_audio("https://youtu.be/abc", tmp_path / "audio.mp3", run=run)
    assert audio.exists() and "-x" in calls[1] and "bestaudio/best" in calls[1]
    sec = await download_section("https://youtu.be/abc", 10, 40.5, tmp_path / "raw.mp4", run=run)
    assert sec == tmp_path / "raw.mp4"
    assert "*10.00-40.50" in calls[2] and "--force-keyframes-at-cuts" in calls[2]
    assert "48K" in calls[1]
    for cmd in calls:
        assert cmd[-2] == "--" and cmd[-1] == "https://youtu.be/abc"


def test_parse_live_chat_keeps_only_reactions():
    def line(ms, text):
        return json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": str(ms), "actions": [
            {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
                "message": {"runs": [{"text": text}]}}}}}]}})

    lines = [line(5000, "KKKKKK"), line(6000, "boa noite"), line(7500, "mds 😂"), "lixo"]
    assert parse_live_chat(lines) == [5.0, 7.5]


def test_parse_live_chat_is_defensive_per_line():
    good = json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": "9000", "actions": [
        {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
            "message": {"runs": [{"text": "kkkkk"}]}}}}}]}})
    emoji_no_shortcuts = json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": "1000",
        "actions": [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
            "message": {"runs": [{"emoji": {"shortcuts": []}}]}}}}}]}})
    null_offset = json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": None,
                                                       "actions": []}})
    negative = json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": "-500", "actions": [
        {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
            "message": {"runs": [{"text": "kkkkk"}]}}}}}]}})
    assert parse_live_chat([emoji_no_shortcuts, null_offset, negative, "[]", good]) == [9.0]


async def test_fetch_youtube_chat_parses_in_a_thread(tmp_path):
    import threading
    from liveclips.ondemand import ingest
    seen = []
    real = ingest.parse_live_chat
    ingest.parse_live_chat = lambda f: (seen.append(threading.current_thread()), real(f))[1]
    try:
        async def run(*cmd, timeout=300):
            (tmp_path / "chat.live_chat.json").write_text("lixo" + chr(10), encoding="utf-8")
            return ""
        assert await ingest.fetch_youtube_chat("u", tmp_path, run=run) == []
    finally:
        ingest.parse_live_chat = real
    assert seen and seen[0] is not threading.main_thread()


async def test_cookies_file_is_used_when_present(tmp_path, monkeypatch):
    from liveclips.ondemand import ingest

    calls = []

    async def run(*cmd, timeout=300):
        calls.append(cmd)
        return json.dumps(info())

    cookies = tmp_path / "youtube-cookies.txt"
    monkeypatch.setattr(ingest, "COOKIES_FILE", cookies)
    await probe("https://youtu.be/abc", run=run)
    assert "--cookies" not in calls[-1]  # sem arquivo, não passa nada
    cookies.write_text("# Netscape HTTP Cookie File\n")
    await probe("https://youtu.be/abc", run=run)
    cmd = calls[-1]
    assert cmd[cmd.index("--cookies") + 1] == str(cookies)
    assert cmd[-2:] == ("--", "https://youtu.be/abc")  # o link continua por último


def test_friendly_errors():
    from liveclips.ondemand.ingest import friendly_error

    assert "idade" in friendly_error("ERROR: [youtube] x: Sign in to confirm your age. This...")
    assert "robô" in friendly_error("ERROR: Sign in to confirm you’re not a bot")
    assert "privado" in friendly_error("ERROR: [youtube] x: Private video. Sign in")
    assert "indisponível" in friendly_error("ERROR: [youtube] x: Video unavailable")
    assert friendly_error("ffmpeg falhou (1): qualquer coisa") is None


def test_live_start_is_read_only_for_lives():
    m = parse_metadata(info(is_live=True, release_timestamp=1700000000), "u")
    assert m.live_start == 1700000000.0
    assert parse_metadata(info(is_live=True, timestamp=5), "u").live_start == 5.0
    assert parse_metadata(info(release_timestamp=9), "u").live_start is None


async def test_window_flags_and_live_delegates_to_ytlive(tmp_path, monkeypatch):
    calls = []

    async def run(*cmd, timeout=300):
        calls.append(cmd)
        out = Path(cmd[cmd.index("-o") + 1].replace("%(ext)s", "mp3"))
        out.write_bytes(b"x")
        return ""

    await download_audio("u", tmp_path / "a.mp3", run=run, start=0, end=7200.5)
    assert "*0.00-7200.50" in calls[-1] and "--live-from-start" not in calls[-1]
    await download_audio("u", tmp_path / "b.mp3", run=run)
    assert "--download-sections" not in calls[-1] and "--live-from-start" not in calls[-1]

    from liveclips.ondemand import ytlive
    seen = []

    async def fake_audio(url, dest, s, e, http, run=None):
        seen.append(("audio", url, s, e, http))
        return dest

    async def fake_section(url, s, e, dest, http, run=None):
        seen.append(("section", url, s, e, http))
        return dest

    monkeypatch.setattr(ytlive, "download_live_audio", fake_audio)
    monkeypatch.setattr(ytlive, "download_live_section", fake_section)
    n = len(calls)
    sentinel = object()
    await download_audio("u", tmp_path / "c.mp3", run=run, start=0, end=60, live=True, http=sentinel)
    await download_section("u", 10, 40, tmp_path / "raw.mp4", run=run, live=True, http=sentinel)
    assert len(calls) == n  # nenhum yt-dlp de download
    assert seen == [("audio", "u", 0, 60, sentinel), ("section", "u", 10, 40, sentinel)]
