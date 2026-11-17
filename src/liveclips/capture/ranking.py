from __future__ import annotations

import math
from datetime import datetime

from ..core.models import Candidate

# Clipes da mesma origem marcam o momento com precisão; entre origens diferentes
# (bot no pico vs. espectador que clipa minutos depois) a defasagem é maior.
SAME_SOURCE_WINDOW_S = 30.0
CROSS_SOURCE_WINDOW_S = 180.0


def compute_rank(c: Candidate, now: datetime) -> float:
    if c.source == "chat_spike":
        return c.spike_score
    age_min = max(1.0, (now - c.created_at).total_seconds() / 60)
    return (c.view_count / age_min) * math.log10(max(c.viewers, 10))


def find_overlap(new: Candidate, existing: list[Candidate],
                 same_source_s: float = SAME_SOURCE_WINDOW_S,
                 cross_source_s: float = CROSS_SOURCE_WINDOW_S) -> Candidate | None:
    for e in existing:
        if e.clip_id == new.clip_id or e.channel.lower() != new.channel.lower():
            continue
        window = same_source_s if e.source == new.source else cross_source_s
        if abs((e.created_at - new.created_at).total_seconds()) <= window:
            return e
    return None
