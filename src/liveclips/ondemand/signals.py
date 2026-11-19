"""Sinais de interesse, 1 ponto por segundo: volume, "mais repetidos" e chat."""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np

from ..processing.ffmpeg import run

WEIGHTS = {"heatmap": 0.45, "chat": 0.35, "loudness": 0.20}
AGREEMENT_BONUS = 0.15
SAMPLE_RATE = 2000  # basta para o RMS por segundo; 4 h em 8 kHz passavam de 1 GB de RAM
BLOCK_S = 600  # segundos de áudio lidos por vez


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


def _rms_db_from_raw(raw: Path) -> np.ndarray:
    samples = np.memmap(raw, dtype=np.int16, mode="r") if raw.stat().st_size else np.zeros(0)
    n = samples.size // SAMPLE_RATE
    out = np.zeros(n)
    for b in range(0, n, BLOCK_S):
        e = min(b + BLOCK_S, n)
        block = np.asarray(samples[b * SAMPLE_RATE:e * SAMPLE_RATE], dtype=np.float64)
        out[b:e] = np.sqrt((block.reshape(e - b, SAMPLE_RATE) ** 2).mean(axis=1))
    del samples
    return 20 * np.log10(out / 32768 + 1e-9)


async def rms_db_per_second(audio: Path, work_dir: Path, run=run) -> np.ndarray:
    raw = work_dir / "audio.raw"
    await run("ffmpeg", "-y", "-i", str(audio), "-f", "s16le", "-ac", "1",
              "-ar", str(SAMPLE_RATE), str(raw), timeout=1800)
    try:
        return await asyncio.to_thread(_rms_db_from_raw, raw)
    finally:
        raw.unlink(missing_ok=True)
