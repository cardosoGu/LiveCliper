"""Entrada do sob demanda: metadados, áudio, trechos e chat (yt-dlp)."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..processing.ffmpeg import MediaError, run

log = logging.getLogger(__name__)

REACTION = re.compile(r"(k{3,}|rs{2,}|hah|hue|\bmds\b|pqp|caralho|nossa|😂|🤣|\blul\b|kekw|"
                      r"omegalul)", re.IGNORECASE)


@dataclass
class VideoMeta:
    url: str
    title: str
    channel: str
    platform: str  # youtube | twitch | kick | outro
    duration: float
    is_live: bool
    heatmap: list[dict] | None
    video_id: str


def parse_metadata(info: dict, url: str) -> VideoMeta:
    extractor = (info.get("extractor_key") or info.get("extractor") or "").lower()
    platform = next((p for p in ("youtube", "twitch", "kick") if p in extractor), "outro")
    channel = info.get("channel") or info.get("uploader") or info.get("uploader_id") or "?"
    return VideoMeta(url=url, title=info.get("title") or "", channel=str(channel),
                     platform=platform, duration=float(info.get("duration") or 0),
                     is_live=bool(info.get("is_live")), heatmap=info.get("heatmap") or None,
                     video_id=str(info.get("id") or ""))


async def probe(url: str, run=run) -> VideoMeta:
    out = await run("yt-dlp", "-J", "--no-playlist", "--no-warnings", "--", url, timeout=120)
    return parse_metadata(json.loads(out), url)


async def download_audio(url: str, dest: Path, run=run) -> Path:
    """Só o áudio, já em MP3 mono 16 kHz 48 kbps (o formato que a transcrição usa)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    await run("yt-dlp", "--no-playlist", "--quiet", "-f", "bestaudio/best", "-x",
              "--audio-format", "mp3", "--audio-quality", "48K", "--postprocessor-args",
              "ffmpeg:-ac 1 -ar 16000 -b:a 48k",
              "-o", str(dest.with_suffix(".%(ext)s")), "--", url, timeout=3600)
    out = dest.with_suffix(".mp3")
    if not out.exists():
        raise MediaError("download do áudio não gerou arquivo")
    return out


async def download_section(url: str, start: float, end: float, dest: Path, run=run) -> Path:
    """Baixa só o trecho [start, end] (em segundos), até 1080p."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    await run("yt-dlp", "--no-playlist", "--quiet",
              "-f", "bv*[height<=1080]+ba/b[height<=1080]/best",
              "--download-sections", f"*{start:.2f}-{end:.2f}", "--force-keyframes-at-cuts",
              "--merge-output-format", "mp4", "-o", str(dest), "--", url, timeout=900)
    if not dest.exists():
        raise MediaError("download do trecho não gerou arquivo")
    return dest


def _message_text(action: dict) -> str:
    parts = []
    for a in action.get("actions", []):
        renderer = (a.get("addChatItemAction", {}).get("item", {})
                    .get("liveChatTextMessageRenderer", {}))
        for run_ in renderer.get("message", {}).get("runs", []):
            parts.append(run_.get("text") or run_.get("emoji", {}).get("shortcuts", [""])[0])
    return " ".join(p for p in parts if p)


def parse_live_chat(lines: Iterable[str]) -> list[float]:
    """Replay do chat do YouTube (uma linha JSON por item) → segundos das reações."""
    times = []
    for line in lines:
        try:
            action = json.loads(line).get("replayChatItemAction", {})
            ms = int(action.get("videoOffsetTimeMsec", 0))
        except (ValueError, AttributeError):
            continue
        if REACTION.search(_message_text(action)):
            times.append(ms / 1000)
    return times


async def fetch_youtube_chat(url: str, work_dir: Path, run=run) -> list[float]:
    """Chat do replay (só YouTube). Sem chat ou com erro: lista vazia (o sinal é opcional)."""
    try:
        await run("yt-dlp", "--no-playlist", "--quiet", "--skip-download", "--write-subs",
                  "--sub-langs", "live_chat", "-o", str(work_dir / "chat"), "--", url,
                  timeout=1800)
        path = work_dir / "chat.live_chat.json"
        if not path.exists():
            return []
        with path.open(encoding="utf-8", errors="ignore") as f:
            return parse_live_chat(f)
    except Exception as e:
        log.info("chat do replay indisponível: %s", e)
        return []
