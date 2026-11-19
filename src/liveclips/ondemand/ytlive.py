"""Janela de uma live do YouTube a partir do início, por fragmentos DASH numerados.

O yt-dlp recusa --download-sections junto com --live-from-start ("This format cannot be
partially downloaded"), então baixamos nós mesmos os fragmentos de 5 s (`&sq=N`; sq=0 é o
início da live) e juntamos com o ffmpeg."""
from __future__ import annotations

import asyncio
import json
import logging
import math
from pathlib import Path

import httpx

from ..processing.ffmpeg import MediaError, run

log = logging.getLogger(__name__)

DASH = "http_dash_segments_generator"
DEFAULT_TD = 5.0
ATTEMPTS = 3


def pick_formats(info: dict) -> tuple[dict, dict]:
    """(áudio, vídeo) DASH da live: áudio de maior abr; vídeo avc1 de maior altura ≤ 1080."""
    fmts = [f for f in info.get("formats") or [] if f.get("protocol") == DASH and f.get("url")]
    audios = [f for f in fmts if f.get("vcodec") == "none"]
    videos = [f for f in fmts if str(f.get("vcodec") or "").startswith("avc1")
              and (f.get("height") or 0) <= 1080]
    if not audios or not videos:
        raise MediaError("live sem formato DASH para baixar do início")
    return (max(audios, key=lambda f: f.get("abr") or 0),
            max(videos, key=lambda f: f.get("height") or 0))


def segment_range(start: float, end: float, td: float) -> range:
    return range(math.floor(start / td), math.ceil(end / td))


async def _fetch_one(http: httpx.AsyncClient, fmt: dict, n: int, last: bool,
                     retry_wait: float) -> bytes:
    url = f"{fmt['url']}&sq={n}"
    err = "?"
    for attempt in range(ATTEMPTS):
        try:
            r = await http.get(url, headers=fmt.get("http_headers"))
            if r.status_code == 404 and last:
                return b""  # o último fragmento ainda não existe
            r.raise_for_status()
            return r.content
        except httpx.HTTPError as e:
            err = str(e) or type(e).__name__
            if attempt < ATTEMPTS - 1:
                await asyncio.sleep(retry_wait)
    raise MediaError(f"fragmento {n} da live falhou: {err}")


async def fetch_segments(http: httpx.AsyncClient, fmt: dict, seq: range, dest: Path,
                         concurrency: int = 8, retry_wait: float = 1.0) -> Path:
    """Baixa os fragmentos de `seq` (até `concurrency` por vez) e grava em ordem em `dest`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    seqs = list(seq)
    with dest.open("wb") as f:
        for i in range(0, len(seqs), concurrency):
            batch = seqs[i:i + concurrency]
            parts = await asyncio.gather(*(
                _fetch_one(http, fmt, n, n == seqs[-1], retry_wait) for n in batch))
            for p in parts:
                f.write(p)
    return dest


async def probe_live_formats(url: str, run=run) -> dict:
    from .ingest import _yt  # import tardio: o ingest delega para este módulo
    out = await run(*_yt("yt-dlp", "-J", "--live-from-start", "--no-warnings", "--", url),
                    timeout=120)
    return json.loads(out)


def _td(fmt: dict) -> float:
    return float(fmt.get("target_duration") or DEFAULT_TD)


async def _fetch_window(http: httpx.AsyncClient, fmt: dict, start: float, end: float,
                        dest: Path) -> float:
    """Baixa a janela do formato; devolve o deslocamento do start dentro do 1º fragmento."""
    td = _td(fmt)
    seq = segment_range(start, end, td)
    await fetch_segments(http, fmt, seq, dest)
    return start - seq.start * td


async def download_live_audio(url: str, dest: Path, start: float, end: float,
                              http: httpx.AsyncClient, run=run) -> Path:
    """Áudio da janela [start, end] da live, em MP3 mono 16 kHz 48 kbps."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    audio, _ = pick_formats(await probe_live_formats(url, run))
    tmp = dest.with_suffix(".part.m4a")
    out = dest.with_suffix(".mp3")
    try:
        off = await _fetch_window(http, audio, start, end, tmp)
        await run("ffmpeg", "-y", "-ss", f"{off:.3f}", "-i", str(tmp),
                  "-t", f"{end - start:.3f}", "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k",
                  str(out), timeout=3600)
    finally:
        tmp.unlink(missing_ok=True)
    if not out.exists():
        raise MediaError("download do áudio não gerou arquivo")
    return out


async def download_live_section(url: str, start: float, end: float, dest: Path,
                                http: httpx.AsyncClient, run=run) -> Path:
    """Trecho [start, end] da live (vídeo até 1080p + áudio), reencodado para corte exato."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    audio, video = pick_formats(await probe_live_formats(url, run))
    v_tmp, a_tmp = dest.with_suffix(".part.mp4"), dest.with_suffix(".part.m4a")
    try:
        v_off = await _fetch_window(http, video, start, end, v_tmp)
        a_off = await _fetch_window(http, audio, start, end, a_tmp)
        await run("ffmpeg", "-y", "-ss", f"{v_off:.3f}", "-i", str(v_tmp),
                  "-ss", f"{a_off:.3f}", "-i", str(a_tmp),
                  "-t", f"{end - start:.3f}", "-map", "0:v", "-map", "1:a",
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                  "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest),
                  timeout=3600)
    finally:
        v_tmp.unlink(missing_ok=True)
        a_tmp.unlink(missing_ok=True)
    if not dest.exists():
        raise MediaError("download do trecho não gerou arquivo")
    return dest
