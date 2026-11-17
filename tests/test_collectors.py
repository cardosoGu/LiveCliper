import json
from datetime import timedelta
from pathlib import Path

import httpx
import respx

from liveclips.collectors import (KICK_CLIPS_URL, collect_kick, collect_twitch_public,
                                  parse_kick_clips)
from liveclips.models import State
from liveclips.timeutil import parse_iso

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "kick_clips.json").read_text())


def clip(cid, lang, name="Gaules", secs=0):
    return {"id": cid, "url": f"https://clips.twitch.tv/{cid}", "broadcaster_name": name,
            "created_at": f"2026-11-19T03:00:{secs:02d}Z", "duration": 30, "title": "",
            "view_count": 50, "language": lang}


class FakeAPI:
    async def get_clips(self, game_id, started_at, ended_at):
        return [clip("a", "pt"), clip("b", "en", name="Other"), clip("c", "pt", name="Loud")]


async def test_twitch_public_keeps_only_portuguese(db):
    now = parse_iso("2026-11-19T03:05:00Z")
    added = await collect_twitch_public(FakeAPI(), "g", db, {"gaules": 5000}, now)
    assert added == 2
    ids = {c.clip_id for c in db.by_state(State.DETECTED)}
    assert ids == {"twitch:a", "twitch:c"}


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
