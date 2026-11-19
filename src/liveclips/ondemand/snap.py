"""Bordas do corte: começa e termina em frase/pausa, nunca no meio de uma palavra."""
from __future__ import annotations

import math

import numpy as np

PAUSE = 0.5
MIN_PRE_ROLL = 6.0
MIN_POST_ROLL = 3.0
SEARCH = 8.0
MAX_LEN = 65.0
MIN_LEN = 15.0
REACTION_MAX = 6.0


def ends_sentence(word: str) -> bool:
    w = word.strip()
    return bool(w) and w[-1] in ".?!" and not w.endswith("...") and not w.endswith("…")


def _boundary_after(words: list[dict], i: int) -> bool:
    """Depois da palavra i termina uma frase (pontuação ou pausa)."""
    if i == len(words) - 1:
        return True
    return ends_sentence(words[i]["word"]) or words[i + 1]["start"] - words[i]["end"] >= PAUSE


def sentence_starts(words: list[dict]) -> list[float]:
    if not words:
        return []
    return [words[0]["start"]] + [words[i]["start"] for i in range(1, len(words))
                                  if _boundary_after(words, i - 1)]


def sentence_ends(words: list[dict]) -> list[float]:
    return [w["end"] for i, w in enumerate(words) if _boundary_after(words, i)]


def sentences(words: list[dict]) -> list[tuple[float, float, str]]:
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        if _boundary_after(words, i):
            out.append((cur[0]["start"], cur[-1]["end"],
                        " ".join(x["word"].strip() for x in cur)))
            cur = []
    return out


def _nearest(cands: list[float], target: float, lo: float, hi: float) -> float | None:
    inside = [c for c in cands if lo <= c <= hi]
    return min(inside, key=lambda c: abs(c - target)) if inside else None


def extend_past_reaction(end: float, words: list[dict], rms_db: np.ndarray | None) -> float:
    """Não corta a risada: estende enquanto o volume fica 3 dB acima da base local,
    até 6 s e sem invadir a próxima palavra."""
    if rms_db is None or rms_db.size == 0:
        return end
    next_start = next((w["start"] for w in words
                       if w["start"] >= end - 1e-6 and w["end"] > end + 1e-6),
                      end + REACTION_MAX)
    if next_start - end < 0.05:  # a próxima palavra já começa: nada a estender
        return end
    limit = min(end + REACTION_MAX, next_start - 0.05, float(rms_db.size))
    i0 = int(end)
    base = float(np.median(rms_db[max(0, i0 - 45): i0 + 45]))
    t = end
    sec = math.ceil(end)  # o segundo que ainda contém a fala não conta
    while sec < limit and sec < rms_db.size and rms_db[sec] > base + 3:
        t = min(limit, sec + 1.0)
        sec += 1
    return max(end, t)


def snap_clip(words: list[dict], peak: float, start: float, end: float,
              rms_db: np.ndarray | None = None) -> tuple[float, float] | None:
    if not words:
        return None
    starts, ends = sentence_starts(words), sentence_ends(words)
    s_hi, e_lo = peak - MIN_PRE_ROLL, peak + MIN_POST_ROLL
    s = _nearest(starts, start, start - SEARCH, min(start + SEARCH, s_hi))
    if s is None:
        s = _nearest([w["start"] for w in words], start, start - SEARCH, s_hi)
    e = _nearest(ends, end, max(end - SEARCH, e_lo), end + SEARCH)
    if e is None:
        e = _nearest([w["end"] for w in words], end, e_lo, end + SEARCH)
    if s is None or e is None:
        return None
    if e - s > MAX_LEN:  # o maior trecho ≤ 65 s que ainda contém o pico
        pairs = [(a, b) for a in starts if s <= a <= s_hi
                 for b in ends if e_lo <= b <= e and b - a <= MAX_LEN]
        if not pairs:
            return None
        s, e = max(pairs, key=lambda p: p[1] - p[0])
    e = min(extend_past_reaction(e, words, rms_db), s + MAX_LEN)
    if e - s < MIN_LEN:
        return None
    return round(s, 2), round(e, 2)
