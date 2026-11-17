from liveclips.capture.twitch.chat import ChannelInfo
from liveclips.capture.twitch.clipper import Clipper, PendingSpike, TokenBucket, choose_spike
from liveclips.capture.twitch.discovery import discover_twitch
from liveclips.core.models import State
from liveclips.capture.spike import Spike
from liveclips.capture.twitch.api import TwitchError


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


async def nosleep(_):
    return None


SPIKE = Spike(at=0, ratio=10, keyword_bonus=0.5, msgs=50)
CLIP = {"id": "c1", "url": "https://clips.twitch.tv/c1", "broadcaster_name": "Gaules",
        "created_at": "2026-11-19T03:00:00Z", "duration": 30, "title": "t", "view_count": 0}


class FakeAPI:
    def __init__(self, errors=()):
        self.errors = list(errors)
        self.create_calls = 0

    async def create_clip(self, broadcaster_id):
        self.create_calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "c1"

    async def get_clip(self, clip_id):
        return CLIP

    async def get_streams(self, game_id, language="pt", max_items=500):
        return [
            {"user_login": "Small", "user_id": "1", "viewer_count": 10},
            {"user_login": "Big", "user_id": "2", "viewer_count": 9000},
            {"user_login": "Blocked", "user_id": "3", "viewer_count": 99999},
        ]


def test_token_bucket():
    clock = Clock(0)
    b = TokenBucket(20, clock)
    assert b.try_take()
    assert not b.try_take()
    clock.t = 20
    assert b.try_take()


def test_choose_spike_drops_old_and_picks_best():
    info = ChannelInfo("a", "1", 100)
    old = PendingSpike(info, SPIKE, 99, received=0)
    low = PendingSpike(info, SPIKE, 1, received=95)
    high = PendingSpike(info, SPIKE, 5, received=96)
    best, rest = choose_spike([old, low, high], now=100)
    assert best is high and rest == [low]


async def test_tick_creates_candidate(db):
    api = FakeAPI()
    clock = Clock()
    clipper = Clipper(api, db, TokenBucket(20, clock), clock, nosleep)
    await clipper.on_spike(ChannelInfo("gaules", "1", 1000), SPIKE)
    await clipper.tick()
    await clipper.drain()
    [c] = db.by_state(State.DETECTED)
    assert c.source == "chat_spike" and c.clip_id == "twitch:c1" and c.spike_score > 0


async def test_forbidden_silences_channel(db):
    api = FakeAPI(errors=[TwitchError(403, "clips disabled")])
    clock = Clock()
    clipper = Clipper(api, db, TokenBucket(0, clock), clock, nosleep)
    info = ChannelInfo("gaules", "1", 1000)
    await clipper.on_spike(info, SPIKE)
    await clipper.tick()
    await clipper.on_spike(info, SPIKE)
    assert clipper.pending == []
    assert db.by_state(State.DETECTED) == []


async def test_503_retries_three_times(db):
    api = FakeAPI(errors=[TwitchError(503, "x")] * 3)
    clock = Clock()
    clipper = Clipper(api, db, TokenBucket(0, clock), clock, nosleep)
    await clipper.on_spike(ChannelInfo("gaules", "1", 1000), SPIKE)
    await clipper.tick()
    assert api.create_calls == 3


async def test_discover_filters_blocked_and_sorts():
    infos = await discover_twitch(FakeAPI(), "g", 10, blocked={"blocked"})
    assert [i.login for i in infos] == ["big", "small"]


async def test_waiting_for_clip_does_not_block_next_spike(db):
    import asyncio

    release = asyncio.Event()

    class SlowAPI(FakeAPI):
        async def get_clip(self, clip_id):
            await release.wait()
            return CLIP

    api = SlowAPI()
    clock = Clock()
    clipper = Clipper(api, db, TokenBucket(0, clock), clock, nosleep)
    await clipper.on_spike(ChannelInfo("gaules", "1", 1000), SPIKE)
    await clipper.tick()
    await clipper.on_spike(ChannelInfo("loud", "2", 1000), SPIKE)
    await asyncio.wait_for(clipper.tick(), timeout=1)  # não fica preso no 1º clipe
    assert api.create_calls == 2
    release.set()
    await clipper.drain()
