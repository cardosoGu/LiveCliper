from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from .timeutil import utcnow


class State(StrEnum):
    DETECTED = "detected"
    PROCESSING = "processing"
    QUEUED = "queued"
    PUBLISHED = "published"
    REJECTED_RANK = "rejected_rank"
    REJECTED_AI = "rejected_ai"
    EXPIRED = "expired"
    DELETED = "deleted"
    FAILED = "failed"


@dataclass
class Candidate:
    platform: str  # "twitch" | "kick"
    source: str  # "chat_spike" | "twitch_public" | "kick_public"
    clip_id: str  # único, com prefixo da plataforma
    clip_url: str
    channel: str
    created_at: datetime
    duration: float = 30.0
    title: str = ""
    viewers: int = 0
    view_count: int = 0
    spike_score: float = 0.0
    rank: float = 0.0
    state: State = State.DETECTED
    id: int | None = None
    raw_path: str | None = None
    vertical_path: str | None = None
    transcript: str | None = None
    ai_score: float | None = None
    ai_json: str | None = None
    reason: str | None = None
    updated_at: datetime = field(default_factory=utcnow)


def is_expired(c: Candidate, now: datetime, ttl_min: int) -> bool:
    """O momento perde a graça: idade contada a partir da criação do clipe."""
    return now - c.created_at > timedelta(minutes=ttl_min)
