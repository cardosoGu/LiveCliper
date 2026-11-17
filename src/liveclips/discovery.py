"""Descobre as lives BR da categoria e mantém o monitor de chat atualizado."""
from __future__ import annotations

import asyncio
import logging

from .chat_monitor import ChannelInfo, ChatMonitor
from .db import DB
from .twitch_api import TwitchAPI

log = logging.getLogger(__name__)


async def discover_twitch(api: TwitchAPI, game_id: str, max_channels: int,
                          blocked: set[str]) -> list[ChannelInfo]:
    streams = await api.get_streams(game_id, language="pt", max_items=max_channels * 2)
    infos = [
        ChannelInfo(s["user_login"].lower(), s["user_id"], int(s.get("viewer_count", 0)))
        for s in streams
        if s["user_login"].lower() not in blocked
    ]
    infos.sort(key=lambda i: i.viewers, reverse=True)
    return infos[:max_channels]


async def discovery_loop(api: TwitchAPI, game_id: str, monitor: ChatMonitor, db: DB,
                         max_channels: int, viewers_cache: dict[str, int],
                         interval_s: float = 600) -> None:
    while True:
        infos = await discover_twitch(api, game_id, max_channels, db.blocked_channels())
        monitor.set_channels(infos)
        viewers_cache.clear()
        viewers_cache.update({i.login: i.viewers for i in infos})
        log.info("discovery: %d lives BR monitoradas", len(infos))
        await asyncio.sleep(interval_s)
