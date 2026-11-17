"""Regras de ritmo de publicação (funções puras): aquecimento, intervalo e escolha."""
from __future__ import annotations

from datetime import datetime, timedelta

from ..core.models import Candidate


def daily_limit(day_index: int, start: int, step: int, cap: int) -> int:
    return min(cap, start + step * max(0, day_index))


def can_post_now(now: datetime, last_post_at: datetime | None, posts_today: int,
                 limit: int, min_interval_min: int) -> bool:
    if posts_today >= limit:
        return False
    if last_post_at is None:
        return True
    return now - last_post_at >= timedelta(minutes=min_interval_min)


def pick_next(queue: list[Candidate], last_channel: str | None) -> Candidate | None:
    ordered = sorted(queue, key=lambda c: (c.ai_score or 0.0, c.rank), reverse=True)
    for c in ordered:
        if last_channel is None or c.channel != last_channel:
            return c
    return None
