"""Entrada de candidatos: conversão de clipes e regra de admissão (bloqueio + dedup)."""
from __future__ import annotations

from datetime import datetime, timedelta

from .db import DB
from .models import Candidate, State
from .ranking import compute_rank, find_overlap
from .timeutil import parse_iso, utcnow


def candidate_from_twitch_clip(clip: dict, source: str, viewers: int,
                               spike_score: float = 0.0) -> Candidate:
    return Candidate(
        platform="twitch",
        source=source,
        clip_id=f"twitch:{clip['id']}",
        clip_url=clip["url"],
        channel=clip["broadcaster_name"].lower(),
        created_at=parse_iso(clip["created_at"]),
        duration=float(clip.get("duration") or 30),
        title=clip.get("title") or "",
        viewers=viewers,
        view_count=int(clip.get("view_count") or 0),
        spike_score=spike_score,
    )


def admit(db: DB, c: Candidate, now: datetime | None = None) -> bool:
    """Insere o candidato se não for de canal bloqueado nem um momento já capturado."""
    now = now or utcnow()
    c.channel = c.channel.lower()
    if db.is_blocked(c.channel):
        return False
    c.rank = compute_rank(c, now)
    others = db.channel_candidates_since(c.channel, c.created_at - timedelta(minutes=5))
    dup = find_overlap(c, others)
    if dup is not None:
        if dup.state is State.DETECTED and c.rank > dup.rank:
            dup.state = State.REJECTED_RANK
            dup.reason = "duplicado de um candidato mais forte"
            db.update(dup)
        else:
            return False
    return db.insert_candidate(c) is not None
