"""Assunto longo vira série: 2 ou 3 partes cortadas em fim de frase."""
from __future__ import annotations

from .snap import sentence_ends, sentence_starts

SINGLE_MAX = 65.0
PART_MIN = 25.0
PART_MAX = 58.0
SERIES_MAX = 174.0


def _next_start(starts: list[float], t: float) -> float | None:
    return next((s for s in starts if s >= t - 1e-6), None)


def split_parts(words, start, end):
    if end - start <= SINGLE_MAX:
        return [(start, end)]
    starts, ends = sentence_starts(words), sentence_ends(words)
    cuts = [e for e in ends if start < e < end]
    best = None
    for k in (2, 3):
        options = []
        if k == 2:
            for c1 in cuts:
                s2 = _next_start(starts, c1)
                if s2 is not None:
                    options.append([(start, c1), (s2, end)])
        else:
            for i, c1 in enumerate(cuts):
                s2 = _next_start(starts, c1)
                if s2 is None:
                    continue
                for c2 in cuts[i + 1:]:
                    s3 = _next_start(starts, c2)
                    if s3 is not None and c2 > s2:
                        options.append([(start, c1), (s2, c2), (s3, end)])
        valid = [o for o in options if all(PART_MIN <= b - a <= PART_MAX for a, b in o)]
        if valid:
            best = min(valid, key=lambda o: max(b - a for a, b in o) - min(b - a for a, b in o))
            break
    return [(round(a, 2), round(b, 2)) for a, b in best] if best else None
