"""Remove pastas de mídia antigas para não encher o disco."""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


def cleanup_media(media_dir: Path, now: datetime | None = None,
                  max_age_h: float = 48) -> int:
    cutoff = (now.timestamp() if now else time.time()) - max_age_h * 3600
    removed = 0
    if not media_dir.exists():
        return 0
    for d in media_dir.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


async def cleanup_loop(media_dir: Path, interval_s: float = 3600) -> None:
    while True:
        n = await asyncio.to_thread(cleanup_media, media_dir)
        if n:
            log.info("limpeza: %d pastas de mídia removidas", n)
        await asyncio.sleep(interval_s)
