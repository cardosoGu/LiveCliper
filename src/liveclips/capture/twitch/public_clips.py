"""Clipes que o próprio público criou nas categorias (API oficial da Twitch)."""
from __future__ import annotations

from datetime import datetime, timedelta

from ...core.db import DB
from ..intake import admit, candidate_from_twitch_clip
from .api import TwitchAPI


async def collect_twitch_public(api: TwitchAPI, game_id: str | list[str], db: DB,
                                viewers_cache: dict[str, int], now: datetime,
                                min_viewers: int = 0) -> int:
    clips: list[dict] = []
    for gid in [game_id] if isinstance(game_id, str) else game_id:  # /clips: 1 jogo por vez
        clips += await api.get_clips(gid, now - timedelta(minutes=20), now)
    added = 0
    for clip in clips:
        if (clip.get("language") or "").lower() not in ("pt", "pt-br"):
            continue
        login = clip["broadcaster_name"].lower()
        if min_viewers and viewers_cache.get(login, 0) < min_viewers:
            continue  # live pequena (ou fora das monitoradas): não vale a cota da IA
        c = candidate_from_twitch_clip(clip, "twitch_public", viewers_cache.get(login, 100))
        if admit(db, c, now):
            added += 1
    return added
