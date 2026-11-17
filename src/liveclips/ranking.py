from __future__ import annotations

import math
from datetime import datetime

from .models import Candidate


def compute_rank(c: Candidate, now: datetime) -> float:
    if c.source == "chat_spike":
        return c.spike_score
    age_min = max(1.0, (now - c.created_at).total_seconds() / 60)
    return (c.view_count / age_min) * math.log10(max(c.viewers, 10))


def find_overlap(new: Candidate, existing: list[Candidate],
                 window_s: float = 30.0) -> Candidate | None:
    for e in existing:
        if e.clip_id == new.clip_id or e.channel.lower() != new.channel.lower():
            continue
        if abs((e.created_at - new.created_at).total_seconds()) <= window_s:
            return e
    return None
