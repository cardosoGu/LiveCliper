"""Escolha dos picos da curva de interesse."""
from __future__ import annotations

import numpy as np

from .signals import moving_average


def pick_peaks(score: np.ndarray, max_peaks: int = 20, min_separation: int = 90,
               smooth: int = 5) -> list[int]:
    s = moving_average(np.asarray(score, dtype=float), smooth)
    picked: list[int] = []
    for i in np.argsort(-s, kind="stable"):
        if s[i] <= 0 or len(picked) >= max_peaks:
            break
        if all(abs(int(i) - j) >= min_separation for j in picked):
            picked.append(int(i))
    return sorted(picked)
