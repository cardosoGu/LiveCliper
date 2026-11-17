"""Transcrição com detecção de idioma: Groq (nuvem) → faster-whisper local → sem legenda.

O idioma NÃO é forçado: com `language=pt` o Whisper "aportuguesa" falas em inglês,
o que esconderia clipes de outros idiomas da IA e geraria legendas sem sentido.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger(__name__)
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_LOCAL_MODEL = None
PORTUGUESE = {"pt", "pt-br", "portuguese"}
MIN_WORDS_TO_TRUST_LANGUAGE = 5


@dataclass
class Transcript:
    segments: list[dict] = field(default_factory=list)
    language: str | None = None  # código ou nome devolvido pelo Whisper
    words: list[dict] = field(default_factory=list)  # {"word", "start", "end"} por palavra

    @property
    def text(self) -> str:
        return " ".join(s["text"].strip() for s in self.segments).strip()

    def is_clearly_not_portuguese(self) -> bool:
        """Só afirma 'não é português' com fala suficiente para confiar na detecção."""
        if not self.language or len(self.text.split()) < MIN_WORDS_TO_TRUST_LANGUAGE:
            return False
        return self.language.strip().lower() not in PORTUGUESE


async def transcribe_groq(audio: Path, api_key: str, http: httpx.AsyncClient) -> Transcript:
    r = await http.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        data={"model": "whisper-large-v3", "response_format": "verbose_json",
              "timestamp_granularities[]": ["word", "segment"]},
        files={"file": (audio.name, audio.read_bytes(), "audio/mpeg")},
        timeout=60,
    )
    r.raise_for_status()
    j = r.json()
    segs = [{"start": s["start"], "end": s["end"], "text": s["text"]}
            for s in j.get("segments", [])]
    words = [{"word": w["word"], "start": w["start"], "end": w["end"]}
             for w in j.get("words", [])]
    return Transcript(segs, j.get("language"), words)


def _transcribe_local_sync(audio: Path) -> Transcript:
    global _LOCAL_MODEL
    from faster_whisper import WhisperModel

    if _LOCAL_MODEL is None:
        _LOCAL_MODEL = WhisperModel("small", device="cpu", compute_type="int8")
    segs, info = _LOCAL_MODEL.transcribe(str(audio), vad_filter=True, word_timestamps=True)
    segments, words = [], []
    for s in segs:
        segments.append({"start": s.start, "end": s.end, "text": s.text})
        words += [{"word": w.word, "start": w.start, "end": w.end} for w in (s.words or [])]
    return Transcript(segments, info.language, words)


async def transcribe_local(audio: Path) -> Transcript:
    return await asyncio.to_thread(_transcribe_local_sync, audio)


async def transcribe(audio: Path, groq_key: str, http: httpx.AsyncClient,
                     groq=transcribe_groq, local=transcribe_local) -> Transcript:
    if groq_key:
        try:
            return await groq(audio, groq_key, http)
        except Exception as e:
            log.warning("Groq falhou, usando whisper local: %s", e)
    try:
        return await local(audio)
    except Exception as e:
        log.warning("transcrição local falhou, seguindo sem legenda: %s", e)
        return Transcript()
