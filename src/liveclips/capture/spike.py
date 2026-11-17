"""Detector de pico de chat: compara a taxa de mensagens de cada janela com uma média móvel."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

DEFAULT_KEYWORDS = [
    r"k{3,}", r"kekw", r"\blul\b", r"clipa", r"\bclip\b", r"\bmds\b",
    r"\bn[aã]o+\b", r"\bmano\b", r"pogchamp", r"omegalul", "🤣", "😂",
]


@dataclass(frozen=True)
class Spike:
    at: float
    ratio: float
    keyword_bonus: float
    msgs: int


def spike_score(ratio: float, keyword_bonus: float, viewers: int) -> float:
    return ratio * (1.0 + keyword_bonus) * math.log10(max(viewers, 10))


class SpikeDetector:
    def __init__(self, window_s: float = 10.0, multiplier: float = 3.0, min_msgs: int = 15,
                 cooldown_s: float = 180.0, warmup_windows: int = 6, alpha: float = 1 / 30,
                 keywords: list[str] | None = None):
        self.window_s = window_s
        self.multiplier = multiplier
        self.min_msgs = min_msgs
        self.cooldown_s = cooldown_s
        self.warmup_windows = warmup_windows
        self.alpha = alpha
        self._kw = re.compile("|".join(keywords or DEFAULT_KEYWORDS), re.IGNORECASE)
        self._window_start: float | None = None
        self._count = 0
        self._kw_hits = 0
        self._windows_seen = 0
        self._last_spike = -math.inf
        self._pending: Spike | None = None
        self.baseline: float | None = None

    def add_message(self, ts: float, text: str) -> None:
        if self._window_start is None:
            self._window_start = ts
        self._close_windows(ts)
        self._count += 1
        if self._kw.search(text):
            self._kw_hits += 1

    def poll(self, now: float) -> Spike | None:
        self._close_windows(now)
        spike, self._pending = self._pending, None
        return spike

    def _close_windows(self, now: float) -> None:
        if self._window_start is None:
            return
        while now >= self._window_start + self.window_s:
            self._close_one(self._window_start + self.window_s)
            self._window_start += self.window_s

    def _close_one(self, end: float) -> None:
        count, hits = self._count, self._kw_hits
        self._count = self._kw_hits = 0
        self._windows_seen += 1
        base = max(self.baseline or 0.0, 1.0)
        if (
            self.baseline is not None
            and self._windows_seen > self.warmup_windows
            and count >= self.min_msgs
            and count >= self.multiplier * base
            and end - self._last_spike >= self.cooldown_s
        ):
            self._pending = Spike(at=end, ratio=count / base,
                                  keyword_bonus=hits / count, msgs=count)
            self._last_spike = end
        if self.baseline is None:
            self.baseline = float(count)
        else:
            self.baseline = (1 - self.alpha) * self.baseline + self.alpha * count
