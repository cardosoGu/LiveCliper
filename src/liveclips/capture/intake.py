"""Entrada de candidatos: conversão de clipes e regra de admissão (bloqueio + dedup)."""
from __future__ import annotations

from datetime import datetime, timedelta

from ..core.db import DB
from ..core.models import Candidate, State
from .ranking import compute_rank, find_overlap
from ..core.timeutil import parse_iso, utcnow


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


REFRESHABLE = (State.DETECTED, State.REJECTED_RANK)


def refresh(db: DB, existing: Candidate, seen: Candidate, now: datetime) -> None:
    """Clipe já conhecido visto de novo: atualiza views e rank; se tinha sido
    rejeitado por rank baixo, volta para a fila do pipeline reavaliar."""
    if existing.state not in REFRESHABLE:
        return
    existing.view_count = max(existing.view_count, seen.view_count)
    existing.viewers = seen.viewers or existing.viewers
    new_rank = compute_rank(existing, now)
    if new_rank <= existing.rank and existing.state is State.REJECTED_RANK:
        return
    existing.rank = new_rank
    existing.state = State.DETECTED
    db.update(existing)


def admit(db: DB, c: Candidate, now: datetime | None = None) -> bool:
    """Insere o candidato se não for de canal bloqueado nem um momento já capturado.
    Retorna True só quando um candidato novo entra."""
    now = now or utcnow()
    c.channel = c.channel.lower()
    if db.is_blocked(c.channel):
        return False
    known = db.get_by_clip_id(c.clip_id)
    if known is not None:
        refresh(db, known, c, now)
        return False
    c.rank = compute_rank(c, now)
    window = timedelta(minutes=5)
    others = [o for o in db.channel_candidates_since(c.channel, c.created_at - window)
              if o.created_at <= c.created_at + window]
    dup = find_overlap(c, others)
    if dup is not None:
        if dup.state in REFRESHABLE and c.rank > dup.rank:
            dup.state = State.DUPLICATE
            dup.reason = "mesmo momento de um candidato mais forte"
            db.update(dup)
        else:
            return False
    return db.insert_candidate(c) is not None
