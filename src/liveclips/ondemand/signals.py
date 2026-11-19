"""Sinais de interesse, 1 ponto por segundo: volume, "mais repetidos" e chat."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ..processing.ffmpeg import run

WEIGHTS = {"heatmap": 0.45, "chat": 0.35, "loudness": 0.20}
AGREEMENT_BONUS = 0.15
SAMPLE_RATE = 8000


def robust_norm(x: np.ndarray, low_q: float = 25, high_q: float = 99) -> np.ndarray:
    if x.size == 0:
        return x.astype(float)
    lo, hi = np.percentile(x, low_q), np.percentile(x, high_q)
    return np.clip((x - lo) / max(hi - lo, 1e-9), 0.0, 1.0)


def moving_average(x: np.ndarray, w: int) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    if w <= 1 or x.size == 0:
        return x
    w = min(w, x.size)  # janela maior que o sinal: média do sinal inteiro, mesmo tamanho
    return np.convolve(x, np.ones(w) / w, mode="same")


def loudness_signal(rms_db: np.ndarray) -> np.ndarray:
    """Quanto o volume sobe acima da média do último minuto (risada, grito, aplauso)."""
    baseline = moving_average(rms_db, 61)
    return robust_norm(np.maximum(rms_db - baseline, 0.0))


def heatmap_signal(heatmap: list[dict] | None, n: int) -> np.ndarray | None:
    pts = []
    for item in heatmap or []:
        try:
            pts.append(((float(item["start_time"]) + float(item["end_time"])) / 2,
                        float(item["value"])))
        except (KeyError, TypeError, ValueError):
            continue
    if len(pts) < 2:
        return None
    pts.sort()
    xs, ys = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
    return robust_norm(np.interp(np.arange(n) + 0.5, xs, ys))


def chat_signal(times: list[float], n: int) -> np.ndarray | None:
    if not times or n <= 0:
        return None
    idx = np.clip(np.asarray(times, dtype=int), 0, n - 1)
    counts = np.bincount(idx, minlength=n).astype(float)
    return robust_norm(moving_average(counts, 5))


def combine(signals: dict[str, np.ndarray | None]) -> np.ndarray:
    present = {k: v for k, v in signals.items() if v is not None and v.size}
    if not present:
        return np.zeros(0)
    n = min(v.size for v in present.values())
    total = sum(WEIGHTS[k] for k in present)
    score = sum(WEIGHTS[k] / total * present[k][:n] for k in present)
    agree = sum((present[k][:n] > 0.5).astype(int) for k in present)
    return score + AGREEMENT_BONUS * (agree >= 2)


async def rms_db_per_second(audio: Path, work_dir: Path, run=run) -> np.ndarray:
    raw = work_dir / "audio.raw"
    await run("ffmpeg", "-y", "-i", str(audio), "-f", "s16le", "-ac", "1",
              "-ar", str(SAMPLE_RATE), str(raw), timeout=1800)
    samples = np.fromfile(raw, dtype=np.int16).astype(np.float32)
    raw.unlink(missing_ok=True)
    n = samples.size // SAMPLE_RATE
    if n == 0:
        return np.zeros(0)
    per_sec = samples[: n * SAMPLE_RATE].reshape(n, SAMPLE_RATE)
    rms = np.sqrt((per_sec ** 2).mean(axis=1))
    return 20 * np.log10(rms / 32768 + 1e-9)
