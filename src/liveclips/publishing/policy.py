"""Regra de ritmo de publicação (função pura): intervalo e limite diário."""
from __future__ import annotations

from datetime import datetime, timedelta


def can_post_now(now: datetime, last_post_at: datetime | None, posts_today: int,
                 limit: int, min_interval_min: int) -> bool:
    if posts_today >= limit:
        return False
    if last_post_at is None:
        return True
    return now - last_post_at >= timedelta(minutes=min_interval_min)
