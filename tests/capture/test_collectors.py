import json
from datetime import timedelta
from pathlib import Path

import httpx
import respx

from liveclips.capture.kick.public_clips import KICK_CLIPS_URL, collect_kick, parse_kick_clips
from liveclips.capture.twitch.chat import ChannelInfo
from liveclips.capture.twitch.public_clips import collect_twitch_public
from liveclips.core.models import State
from liveclips.core.timeutil import parse_iso

FIXTURE = json.loads((Path(__file__).parent.parent / "fixtures" / "kick_clips.json").read_text())


def clip(cid, lang, name="Gaules", secs=0):
    return {"id": cid, "url": f"https://clips.twitch.tv/{cid}", "broadcaster_name": name,
            "created_at": f"2026-11-19T03:00:{secs:02d}Z", "duration": 30, "title": "",
            "view_count": 50, "language": lang}


class FakeAPI:
    def __init__(self):
        self.seen = []

    async def get_broadcaster_clips(self, broadcaster_id, started_at, ended_at):
        self.seen.append(broadcaster_id)
        return {"1": [clip("a", "pt"), clip("b", "en")],
                "2": [clip("c", "pt", name="Loud")]}[broadcaster_id]


async def test_twitch_public_queries_each_monitored_channel(db):
    now = parse_iso("2026-11-19T03:05:00Z")
    api = FakeAPI()
    channels = [ChannelInfo("gaules", "1", 5000), ChannelInfo("loud", "2", 3000)]
    added = await collect_twitch_public(api, channels, db, now)
    assert api.seen == ["1", "2"]
    assert added == 2
    got = {c.clip_id: c.viewers for c in db.by_state(State.DETECTED)}
    assert got == {"twitch:a": 5000, "twitch:c": 3000}  # clipe em ingles fica de fora


async def test_twitch_public_survives_one_channel_failing(db):
    class Flaky(FakeAPI):
        async def get_broadcaster_clips(self, broadcaster_id, started_at, ended_at):
            if broadcaster_id == "1":
                raise RuntimeError("boom")
            return await super().get_broadcaster_clips(broadcaster_id, started_at, ended_at)

    now = parse_iso("2026-11-19T03:05:00Z")
    channels = [ChannelInfo("gaules", "1", 5000), ChannelInfo("loud", "2", 3000)]
    assert await collect_twitch_public(Flaky(), channels, db, now) == 1


def test_parse_kick_fixture():
    [c] = parse_kick_clips(FIXTURE)
    assert c.platform == "kick" and c.channel == "streamer_br"
    assert c.clip_url.endswith("video.mp4") and c.view_count == 150


@respx.mock
async def test_collect_kick_inserts_recent(db):
    respx.get(KICK_CLIPS_URL.format(slug="gta")).mock(
        return_value=httpx.Response(200, json=FIXTURE))
    now = parse_iso("2026-11-19T03:10:00Z") + timedelta(minutes=5)
    async with httpx.AsyncClient() as http:
        assert await collect_kick(http, "gta", db, now) == 1
