"""Coleta de clipes já criados pelo público (Twitch oficial; Kick não oficial)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable

import httpx

from .db import DB
from .intake import admit, candidate_from_twitch_clip
from .models import Candidate
from .timeutil import parse_iso, utcnow
from .twitch_api import TwitchAPI

log = logging.getLogger(__name__)

KICK_CLIPS_URL = "https://kick.com/api/v2/categories/{slug}/clips"
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept": "application/json",
}


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


def parse_kick_clips(payload: dict) -> list[Candidate]:
    out = []
    for item in payload.get("clips", []):
        url = item.get("video_url") or item.get("clip_url")
        channel = (item.get("channel") or {}).get("slug")
        if not url or not channel or not item.get("id"):
            continue
        out.append(Candidate(
            platform="kick",
            source="kick_public",
            clip_id=f"kick:{item['id']}",
            clip_url=url,
            channel=channel.lower(),
            created_at=parse_iso(item["created_at"]),
            duration=float(item.get("duration") or 30),
            title=item.get("title") or "",
            viewers=100,
            view_count=int(item.get("view_count") or item.get("views") or 0),
        ))
    return out


async def collect_kick(http: httpx.AsyncClient, slug: str, db: DB, now: datetime) -> int:
    r = await http.get(KICK_CLIPS_URL.format(slug=slug),
                       params={"sort": "date", "time": "day"}, headers=BROWSER_HEADERS)
    r.raise_for_status()
    added = 0
    for c in parse_kick_clips(r.json()):
        if now - c.created_at <= timedelta(minutes=30) and admit(db, c, now):
            added += 1
    return added


async def collector_loop(fn: Callable[[datetime], Awaitable[int]], interval_s: float,
                         name: str = "collector") -> None:
    while True:
        try:
            n = await fn(utcnow())
            if n:
                log.info("%s: %d novos candidatos", name, n)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # coletor nunca derruba o app
            log.warning("%s falhou: %s", name, e)
        await asyncio.sleep(interval_s)
