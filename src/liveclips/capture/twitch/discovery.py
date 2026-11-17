"""Descobre as lives BR das categorias e mantém o monitor de chat atualizado."""
from __future__ import annotations

import asyncio
import logging

from .chat import ChannelInfo, ChatMonitor
from ...core.db import DB
from .api import TwitchAPI

log = logging.getLogger(__name__)


async def discover_twitch(api: TwitchAPI, game_id: str | list[str], max_channels: int,
                          blocked: set[str], min_viewers: int = 0) -> list[ChannelInfo]:
    streams = await api.get_streams(game_id, language="pt", max_items=max_channels * 2)
    infos = [
        ChannelInfo(s["user_login"].lower(), s["user_id"], int(s.get("viewer_count", 0)))
        for s in streams
        if s["user_login"].lower() not in blocked
        and int(s.get("viewer_count", 0)) >= min_viewers
    ]
    infos.sort(key=lambda i: i.viewers, reverse=True)
    return infos[:max_channels]


async def discovery_loop(api: TwitchAPI, game_id: str | list[str], monitor: ChatMonitor, db: DB,
                         max_channels: int, viewers_cache: dict[str, int],
                         interval_s: float = 600, min_viewers: int = 0) -> None:
    while True:
        infos = await discover_twitch(api, game_id, max_channels, db.blocked_channels(),
                                      min_viewers)
        monitor.set_channels(infos)
        viewers_cache.clear()
        viewers_cache.update({i.login: i.viewers for i in infos})
        log.info("discovery: %d lives BR monitoradas", len(infos))
        await asyncio.sleep(interval_s)
