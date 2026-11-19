"""Transcrição de vídeos longos: o áudio vai em pedaços (limite de 25 MB do Groq)."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ..processing.ffmpeg import run
from ..processing.subtitles import words_from_segments
from ..processing.transcribe import Transcript, transcribe

CHUNK_S = 1200  # 20 min a 48 kbps ≈ 7 MB


async def split_audio(src: Path, out_dir: Path, chunk_s: int = CHUNK_S, run=run) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("part_*.mp3"):
        old.unlink()
    await run("ffmpeg", "-y", "-i", str(src), "-f", "segment", "-segment_time", str(chunk_s),
              "-c", "copy", str(out_dir / "part_%03d.mp3"), timeout=600)
    return sorted(out_dir.glob("part_*.mp3"))


async def transcribe_long(audio: Path, groq_key: str, http, work_dir: Path,
                          transcribe_fn=transcribe, split=split_audio,
                          chunk_s: int = CHUNK_S) -> Transcript:
    parts = await split(audio, work_dir / "parts", chunk_s)
    segments, words, langs = [], [], []
    for i, part in enumerate(parts):
        t = await transcribe_fn(part, groq_key, http)
        if not t.segments and t.language is None:
            # transcribe() engole falhas e devolve Transcript() vazio: falha alto, não buraco
            raise RuntimeError(f"transcrição falhou no pedaço {i + 1} de {len(parts)}")
        off = i * chunk_s
        segments += [{**s, "start": s["start"] + off, "end": s["end"] + off}
                     for s in t.segments]
        part_words = t.words or words_from_segments(t.segments)
        words += [{**w, "start": w["start"] + off, "end": w["end"] + off} for w in part_words]
        if t.language:
            langs.append(t.language)
    language = Counter(langs).most_common(1)[0][0] if langs else None
    return Transcript(segments, language, words)


def dump_transcript(t: Transcript, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"segments": t.segments, "language": t.language,
                               "words": t.words}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # troca atômica: queda no meio nunca deixa cache corrompido


def load_transcript(path: Path) -> Transcript:
    d = json.loads(path.read_text(encoding="utf-8"))
    return Transcript(d["segments"], d["language"], d["words"])
