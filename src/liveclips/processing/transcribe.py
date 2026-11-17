"""Transcrição em PT-BR: Groq (nuvem) → faster-whisper local → sem legenda."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

log = logging.getLogger(__name__)
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_LOCAL_MODEL = None


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def segments_to_srt(segments: list[dict]) -> str:
    lines: list[str] = []
    n = 1
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        lines += [str(n), f"{_ts(seg['start'])} --> {_ts(seg['end'])}", text, ""]
        n += 1
    return "\n".join(lines)


async def transcribe_groq(audio: Path, api_key: str, http: httpx.AsyncClient) -> list[dict]:
    r = await http.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        data={"model": "whisper-large-v3", "language": "pt",
              "response_format": "verbose_json"},
        files={"file": (audio.name, audio.read_bytes(), "audio/mpeg")},
        timeout=60,
    )
    r.raise_for_status()
    return [{"start": s["start"], "end": s["end"], "text": s["text"]}
            for s in r.json().get("segments", [])]


def _transcribe_local_sync(audio: Path) -> list[dict]:
    global _LOCAL_MODEL
    from faster_whisper import WhisperModel

    if _LOCAL_MODEL is None:
        _LOCAL_MODEL = WhisperModel("small", device="cpu", compute_type="int8")
    segs, _ = _LOCAL_MODEL.transcribe(str(audio), language="pt", vad_filter=True)
    return [{"start": s.start, "end": s.end, "text": s.text} for s in segs]


async def transcribe_local(audio: Path) -> list[dict]:
    return await asyncio.to_thread(_transcribe_local_sync, audio)


async def transcribe(audio: Path, groq_key: str, http: httpx.AsyncClient,
                     groq=transcribe_groq, local=transcribe_local) -> list[dict]:
    if groq_key:
        try:
            return await groq(audio, groq_key, http)
        except Exception as e:
            log.warning("Groq falhou, usando whisper local: %s", e)
    try:
        return await local(audio)
    except Exception as e:
        log.warning("transcrição local falhou, seguindo sem legenda: %s", e)
        return []
