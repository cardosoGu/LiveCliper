"""Entrada do sob demanda: metadados, áudio, trechos e chat (yt-dlp)."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import httpx

from ..processing.ffmpeg import MediaError, run

log = logging.getLogger(__name__)

# Cookies de uma conta do YouTube (formato Netscape), para vídeos com restrição de idade
# ou quando o YouTube pede "confirme que você não é um robô". O main aponta para
# <DATA_DIR>/youtube-cookies.txt; o arquivo é lido a cada chamada (não precisa reiniciar).
COOKIES_FILE: Path | None = None

_FRIENDLY = [
    ("confirm your age", "vídeo com restrição de idade: o YouTube exige login. Coloque os "
     "cookies de uma conta do YouTube em data/youtube-cookies.txt e mande o link de novo"),
    ("not a bot", "o YouTube pediu para confirmar que não é um robô. Coloque os cookies de "
     "uma conta do YouTube em data/youtube-cookies.txt e mande o link de novo"),
    ("private video", "vídeo privado: só dá para cortar vídeos públicos ou não listados"),
    ("members-only", "vídeo só para membros do canal"),
    ("video unavailable", "vídeo indisponível (removido, bloqueado ou link errado)"),
    ("not currently live", "esse canal não está ao vivo agora; mande o link de um vídeo ou VOD"),
    ("unsupported url", "link não suportado: use YouTube, VOD da Twitch ou da Kick"),
]


def friendly_error(text: str) -> str | None:
    """Erro do yt-dlp em português, quando é um caso conhecido."""
    low = text.lower()
    return next((msg for key, msg in _FRIENDLY if key in low), None)


def _yt(*args: str) -> tuple[str, ...]:
    """Comando do yt-dlp com os cookies (se o arquivo existir) antes do "--" e do link."""
    if COOKIES_FILE is not None and Path(COOKIES_FILE).exists():
        i = args.index("--")
        return (*args[:i], "--cookies", str(COOKIES_FILE), *args[i:])
    return args


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
    credit_hint: str = ""  # só o que vai depois do host: "Podpah", "channel/UC…" ou o domínio
    live_start: float | None = None  # epoch (s) do início da live; só quando is_live
    live_status: str | None = None  # is_live | is_upcoming | post_live | not_live…


def _url_path(url: str | None) -> str:
    return urlparse(url or "").path.strip("/")


def _credit_hint(info: dict, platform: str, url: str, fallback: str) -> str:
    if platform == "youtube":
        uid = str(info.get("uploader_id") or "")
        if uid.startswith("@"):
            return uid[1:]
        for key in ("channel_url", "uploader_url"):
            path = _url_path(info.get(key))
            if path:
                return path[1:] if path.startswith("@") else path
        return ""  # sem handle nem canal confiável: o crédito não inventa um
    if platform == "outro":
        return (urlparse(url).hostname or "").removeprefix("www.")
    return str(info.get("uploader_id") or fallback)


def parse_metadata(info: dict, url: str) -> VideoMeta:
    extractor = (info.get("extractor_key") or info.get("extractor") or "").lower()
    platform = next((p for p in ("youtube", "twitch", "kick") if p in extractor), "outro")
    channel = info.get("channel") or info.get("uploader") or info.get("uploader_id") or "?"
    is_live = bool(info.get("is_live"))
    live_start = (float(info.get("release_timestamp") or info.get("timestamp") or 0) or None
                  if is_live else None)
    return VideoMeta(url=url, title=info.get("title") or "", channel=str(channel),
                     platform=platform, duration=float(info.get("duration") or 0),
                     is_live=is_live, heatmap=info.get("heatmap") or None,
                     video_id=str(info.get("id") or ""),
                     credit_hint=_credit_hint(info, platform, url, str(channel)),
                     live_start=live_start, live_status=info.get("live_status"))


async def probe(url: str, run=run) -> VideoMeta:
    out = await run(*_yt("yt-dlp", "-J", "--no-playlist", "--no-warnings", "--", url),
                    timeout=120)
    return parse_metadata(json.loads(out), url)


@asynccontextmanager
async def _client(http: httpx.AsyncClient | None):
    if http is not None:
        yield http
        return
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        yield c


async def download_audio(url: str, dest: Path, run=run, start: float | None = None,
                         end: float | None = None, live: bool = False,
                         http: httpx.AsyncClient | None = None) -> Path:
    """Só o áudio, já em MP3 mono 16 kHz 48 kbps (o formato que a transcrição usa).

    Com start/end baixa só essa janela (segundos); com live=True (YouTube) baixa os
    fragmentos numerados da live a partir do início (o yt-dlp não corta esse caso)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if live:
        from . import ytlive
        async with _client(http) as c:
            return await ytlive.download_live_audio(url, dest, start or 0.0, end or 0.0, c,
                                                    run=run)
    opts: list[str] = []
    if start is not None and end is not None:
        opts += ["--download-sections", f"*{start:.2f}-{end:.2f}"]
    await run(*_yt("yt-dlp", "--no-playlist", "--quiet", "-f", "bestaudio/best", "-x",
              "--audio-format", "mp3", "--audio-quality", "48K", "--postprocessor-args",
              "ffmpeg:-ac 1 -ar 16000 -b:a 48k", *opts,
              "-o", str(dest.with_suffix(".%(ext)s")), "--", url), timeout=3600)
    out = dest.with_suffix(".mp3")
    if not out.exists():
        raise MediaError("download do áudio não gerou arquivo")
    return out


async def download_section(url: str, start: float, end: float, dest: Path, run=run,
                           live: bool = False, http: httpx.AsyncClient | None = None) -> Path:
    """Baixa só o trecho [start, end] (em segundos), até 1080p."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if live:
        from . import ytlive
        async with _client(http) as c:
            return await ytlive.download_live_section(url, start, end, dest, c, run=run)
    await run(*_yt("yt-dlp", "--no-playlist", "--quiet",
              "-f", "bv*[height<=1080]+ba/b[height<=1080]/best",
              "--download-sections", f"*{start:.2f}-{end:.2f}", "--force-keyframes-at-cuts",
              "--merge-output-format", "mp4", "-o", str(dest), "--", url), timeout=900)
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
            if ms < 0 or not REACTION.search(_message_text(action)):
                continue
        except (ValueError, TypeError, IndexError, AttributeError, KeyError):
            continue  # linha torta não derruba o sinal do chat inteiro
        times.append(ms / 1000)
    return times


def _parse_chat_file(path: Path) -> list[float]:
    with path.open(encoding="utf-8", errors="ignore") as f:
        return parse_live_chat(f)


async def fetch_youtube_chat(url: str, work_dir: Path, run=run) -> list[float]:
    """Chat do replay (só YouTube). Sem chat ou com erro: lista vazia (o sinal é opcional)."""
    try:
        await run(*_yt("yt-dlp", "--no-playlist", "--quiet", "--skip-download", "--write-subs",
                  "--sub-langs", "live_chat", "-o", str(work_dir / "chat"), "--", url), timeout=1800)
        path = work_dir / "chat.live_chat.json"
        if not path.exists():
            return []
        return await asyncio.to_thread(_parse_chat_file, path)  # replay grande: fora do loop
    except Exception as e:
        log.info("chat do replay indisponível: %s", e)
        return []
