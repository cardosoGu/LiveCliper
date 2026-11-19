import pytest

from liveclips.ondemand.ingest import VideoMeta
from liveclips.ondemand.source import SourceError, resolve_source

H = 3600.0


def meta(**kw):
    base = dict(url="https://www.youtube.com/watch?v=x", title="t", channel="c",
                platform="youtube", duration=600.0, is_live=False, heatmap=None,
                video_id="x", credit_hint="c", live_start=None)
    base.update(kw)
    return VideoMeta(**base)


class FakeTwitch:
    def __init__(self, user=True, vod=True):
        self.user, self.vod = user, vod

    async def get_user(self, login):
        assert login == "gaules"
        return {"id": "42"} if self.user else None

    async def get_live_vod(self, user_id):
        return {"id": "999", "duration": "5h0m0s"} if self.vod else None


async def test_recorded_video():
    s = await resolve_source(meta(), now_ts=0, max_s=4 * H)
    assert (s.url, s.live, s.start, s.end, s.note) == (meta().url, False, 0, 600.0, "")
    with pytest.raises(SourceError, match="longo demais"):
        await resolve_source(meta(duration=5 * H), now_ts=0, max_s=4 * H)
    with pytest.raises(SourceError, match="duração"):
        await resolve_source(meta(duration=0), now_ts=0, max_s=4 * H)


async def test_youtube_live_from_start_until_now():
    s = await resolve_source(meta(is_live=True, duration=0, live_start=1000.0),
                             now_ts=1000.0 + 2 * H, max_s=4 * H)
    assert s.live and (s.start, s.end) == (0, 2 * H) and "do início até agora" in s.note
    long = await resolve_source(meta(is_live=True, duration=0, live_start=0.0),
                                now_ts=6 * H, max_s=4 * H)
    assert (long.start, long.end) == (2 * H, 6 * H) and "últimas 4 h" in long.note
    with pytest.raises(SourceError, match="há quanto tempo"):
        await resolve_source(meta(is_live=True, duration=0), now_ts=10, max_s=4 * H)


async def test_twitch_live_becomes_the_running_vod():
    m = meta(platform="twitch", is_live=True, url="https://www.twitch.tv/gaules", duration=0)
    s = await resolve_source(m, now_ts=0, max_s=4 * H, twitch=FakeTwitch())
    assert s.url == "https://www.twitch.tv/videos/999" and not s.live
    assert (s.start, s.end) == (1 * H, 5 * H)
    with pytest.raises(SourceError, match="VOD"):
        await resolve_source(m, now_ts=0, max_s=4 * H, twitch=FakeTwitch(vod=False))
    with pytest.raises(SourceError, match="TWITCH_CLIENT_ID"):
        await resolve_source(m, now_ts=0, max_s=4 * H, twitch=None)


async def test_kick_and_other_lives_are_refused():
    with pytest.raises(SourceError, match="Kick"):
        await resolve_source(meta(platform="kick", is_live=True), now_ts=0, max_s=4 * H)
    with pytest.raises(SourceError, match="YouTube e na Twitch"):
        await resolve_source(meta(platform="outro", is_live=True), now_ts=0, max_s=4 * H)
