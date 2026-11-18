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


def test_parse_live_chat_keeps_only_reactions():
    def line(ms, text):
        return json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": str(ms), "actions": [
            {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
                "message": {"runs": [{"text": text}]}}}}}]}})

    lines = [line(5000, "KKKKKK"), line(6000, "boa noite"), line(7500, "mds 😂"), "lixo"]
    assert parse_live_chat(lines) == [5.0, 7.5]
