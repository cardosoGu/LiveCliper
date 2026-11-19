"""De onde baixar e qual trecho processar (vídeo gravado ou live em andamento)."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .ingest import VideoMeta
from .topics import fmt_time
from .twitch import parse_twitch_duration


class SourceError(Exception):
    """Motivo (em português) para recusar o link."""


@dataclass
class Source:
    url: str
    live: bool  # YouTube ao vivo: baixar com --live-from-start
    start: float  # segundos desde o início do vídeo/live
    end: float
    note: str = ""


def _live_window(elapsed: float, max_s: float) -> tuple[float, str]:
    if elapsed <= max_s:
        return 0.0, f"🔴 live em andamento: vou cortar do início até agora ({fmt_time(elapsed)})"
    start = elapsed - max_s
    return start, (f"🔴 live com {fmt_time(elapsed)}: vou cortar as últimas {max_s / 3600:g} h "
                   f"({fmt_time(start)} → {fmt_time(elapsed)})")


async def resolve_source(meta: VideoMeta, now_ts: float, max_s: float, twitch=None) -> Source:
    if not meta.is_live:
        if meta.duration <= 0:
            raise SourceError("não consegui ler a duração do vídeo")
        if meta.duration > max_s:
            raise SourceError(f"vídeo longo demais (máximo {max_s / 3600:g} h)")
        return Source(meta.url, False, 0.0, meta.duration)
    if meta.platform == "youtube":
        if meta.live_start is None:
            raise SourceError("não consegui saber há quanto tempo a live está no ar")
        elapsed = max(0.0, now_ts - meta.live_start)
        start, note = _live_window(elapsed, max_s)
        return Source(meta.url, True, start, elapsed, note)
    if meta.platform == "twitch":
        if twitch is None:
            raise SourceError("para cortar live da Twitch em andamento, configure "
                              "TWITCH_CLIENT_ID e TWITCH_CLIENT_SECRET")
        login = urlparse(meta.url).path.strip("/").split("/")[0].lower()
        user = await twitch.get_user(login) if login else None
        vod = await twitch.get_live_vod(user["id"]) if user else None
        if not vod:
            raise SourceError("essa live não está salvando VOD (ou o VOD é só para inscritos); "
                              "mande o link quando ela acabar")
        elapsed = parse_twitch_duration(vod["duration"])
        start, note = _live_window(elapsed, max_s)
        return Source(f"https://www.twitch.tv/videos/{vod['id']}", False, start, elapsed, note)
    if meta.platform == "kick":
        raise SourceError("live da Kick em andamento ainda não dá: mande o link do VOD quando "
                          "a live acabar")
    raise SourceError("live em andamento só funciona no YouTube e na Twitch")
