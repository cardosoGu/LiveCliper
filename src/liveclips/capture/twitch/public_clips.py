"""Clipes que o próprio público criou na categoria (API oficial da Twitch)."""
from __future__ import annotations

from datetime import datetime, timedelta

from ...core.db import DB
from ..intake import admit, candidate_from_twitch_clip
from .api import TwitchAPI


async def collect_twitch_public(api: TwitchAPI, game_id: str, db: DB,
                                viewers_cache: dict[str, int], now: datetime) -> int:
    clips = await api.get_clips(game_id, now - timedelta(minutes=20), now)
    added = 0
    for clip in clips:
        if (clip.get("language") or "").lower() not in ("pt", "pt-br"):
            continue
        login = clip["broadcaster_name"].lower()
        c = candidate_from_twitch_clip(clip, "twitch_public", viewers_cache.get(login, 100))
        if admit(db, c, now):
            added += 1
    return added
