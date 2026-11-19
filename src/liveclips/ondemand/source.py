"""De onde baixar e qual trecho processar (vídeo gravado ou live em andamento)."""
from __future__ import annotations

from dataclasses import dataclass

import httpx
from urllib.parse import urlparse

from ..core.timeutil import parse_iso

from .ingest import VideoMeta
from .topics import fmt_time
from .twitch import TwitchError, parse_twitch_duration


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
            if meta.live_status in ("is_upcoming", "post_live"):
                raise SourceError("essa live ainda não começou ou acabou de terminar; tente de "
                                  "novo em alguns minutos")
            raise SourceError("não consegui ler a duração do vídeo")
        if meta.duration > max_s:
            raise SourceError(f"vídeo longo demais (máximo {max_s / 3600:g} h)")
        return Source(meta.url, False, 0.0, meta.duration)
    if meta.platform == "youtube":
        if meta.live_start is None:
            raise SourceError("não consegui saber há quanto tempo a live está no ar")
        elapsed = max(0.0, now_ts - meta.live_start)
        if elapsed < 60:
            raise SourceError("a live acabou de começar; mande o link de novo daqui a pouco")
        start, note = _live_window(elapsed, max_s)
        return Source(meta.url, True, start, elapsed, note)
    if meta.platform == "twitch":
        if twitch is None:
            raise SourceError("para cortar live da Twitch em andamento, configure "
                              "TWITCH_CLIENT_ID e TWITCH_CLIENT_SECRET")
        login = (meta.credit_hint or urlparse(meta.url).path.strip("/").split("/")[0]).lower()
        no_vod = SourceError("essa live não está salvando VOD (ou o VOD é só para inscritos); "
                             "mande o link quando ela acabar")
        try:
            user = await twitch.get_user(login) if login else None
            vod = await twitch.get_live_vod(user["id"]) if user else None
        except (TwitchError, httpx.HTTPError) as e:
            raise SourceError("não consegui consultar a Twitch agora; tente de novo em alguns "
                              "minutos") from e
        if not vod:
            raise no_vod
        if meta.live_start is not None:
            created = parse_iso(vod["created_at"]).timestamp()
            if abs(created - meta.live_start) > 15 * 60:
                raise no_vod  # o último arquivo é de outra live
        elapsed = parse_twitch_duration(vod["duration"])
        if elapsed <= 0:
            raise SourceError("não consegui saber a duração do VOD da live")
        start, note = _live_window(elapsed, max_s)
        return Source(f"https://www.twitch.tv/videos/{vod['id']}", False, start, elapsed, note)
    if meta.platform == "kick":
        raise SourceError("live da Kick em andamento ainda não dá: mande o link do VOD quando "
                          "a live acabar")
    raise SourceError("live em andamento só funciona no YouTube e na Twitch")
