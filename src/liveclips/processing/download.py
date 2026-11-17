"""Download dos clipes (Twitch, Kick ou URL direta) com yt-dlp."""
from __future__ import annotations

from pathlib import Path

from .ffmpeg import MediaError, run


async def download_clip(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    await run("yt-dlp", "--no-playlist", "--quiet", "-f", "best[height<=1080]/best",
              "--merge-output-format", "mp4", "-o", str(dest), url, timeout=180)
    if not dest.exists():
        raise MediaError("download não gerou arquivo")
    return dest
