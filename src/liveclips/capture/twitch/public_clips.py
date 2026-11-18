"""Clipes que o próprio público criou nas lives monitoradas (API oficial da Twitch).

Busca canal por canal: o /clips por categoria devolve os 100 clipes mais vistos do mundo
todo, e os poucos em português se perdiam no meio (ex.: Just Chatting, 4 de 91).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ...core.db import DB
from ..intake import admit, candidate_from_twitch_clip
from .api import TwitchAPI
from .chat import ChannelInfo

log = logging.getLogger(__name__)


async def collect_twitch_public(api: TwitchAPI, channels: list[ChannelInfo], db: DB,
                                now: datetime) -> int:
    added = 0
    for ch in channels:
        try:
            clips = await api.get_broadcaster_clips(
                ch.broadcaster_id, now - timedelta(minutes=20), now)
        except Exception as e:  # um canal com problema não derruba os outros
            log.warning("clipes de %s falharam: %s", ch.login, e)
            continue
        for clip in clips:
            if (clip.get("language") or "pt").lower() not in ("pt", "pt-br"):
                continue
            if admit(db, candidate_from_twitch_clip(clip, "twitch_public", ch.viewers), now):
                added += 1
    return added
