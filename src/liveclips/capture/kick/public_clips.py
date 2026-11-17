"""Clipes públicos da categoria no Kick (endpoint não oficial; formato a confirmar)."""
from __future__ import annotations

from datetime import datetime, timedelta

import httpx

from ...core.db import DB
from ...core.models import Candidate
from ...core.timeutil import parse_iso
from ..intake import admit

KICK_CLIPS_URL = "https://kick.com/api/v2/categories/{slug}/clips"
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept": "application/json",
}


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
