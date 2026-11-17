"""Loop de publicação: aplica a política e publica em cada plataforma (ou ensaio)."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from ..ai.verdict import Verdict
from ..core.config import Settings
from ..core.db import DB
from ..core.models import Candidate, State, is_expired
from ..core.timeutil import local_day, local_midnight_utc, utcnow
from ..processing.ffmpeg import make_preview
from .caption import build_caption
from .platforms.base import PublishJob
from .policy import can_post_now, daily_limit, pick_next

log = logging.getLogger(__name__)


class Notifier(Protocol):
    async def post_published(self, c, verdict, results, errors) -> None: ...
    async def dry_run_preview(self, c, verdict, caption, preview_path) -> None: ...


class Scheduler:
    def __init__(self, db: DB, cfg: Settings, publishers: list, media, notifier: Notifier,
                 now_fn: Callable[[], datetime] = utcnow,
                 preview_fn: Callable[[Path, Path], Awaitable[Path]] = make_preview):
        self.db = db
        self.cfg = cfg
        self.publishers = publishers
        self.media = media
        self.notifier = notifier
        self.now_fn = now_fn
        self.preview_fn = preview_fn

    def day_index(self, now: datetime) -> int:
        first = self.db.kv_get("first_post_day")
        if not first:
            return 0
        return (local_day(now) - date.fromisoformat(first)).days

    def paused(self) -> bool:
        return self.db.kv_get("paused") == "1"

    async def tick(self) -> bool:
        if self.paused():
            return False
        now = self.now_fn()
        fresh = []
        for c in self.db.by_state(State.QUEUED, 200):
            if is_expired(c, now, self.cfg.queue_ttl_min):
                c.state = State.EXPIRED
                c.reason = "expirou na fila"
                self.db.update(c)
            else:
                fresh.append(c)
        if not fresh:
            return False
        last = self.db.last_publication()
        limit = daily_limit(self.day_index(now), self.cfg.post_max_per_day,
                            self.cfg.post_max_per_day_step, self.cfg.post_max_per_day_cap)
        posts_today = self.db.publications_count_since(local_midnight_utc(now))
        if not can_post_now(now, last[0] if last else None, posts_today, limit,
                            self.cfg.post_min_interval_min):
            return False
        c = pick_next(fresh, last[1] if last else None)
        if c is None:
            return False
        await self.publish(c, now)
        return True

    async def publish(self, c: Candidate, now: datetime) -> None:
        verdict = Verdict.model_validate_json(c.ai_json)
        caption = build_caption(verdict, c.platform, c.channel)
        if self.cfg.dry_run:
            await self._dry_run(c, verdict, caption, now)
            return
        results, errors = [], []
        try:
            url = await self.media.expose(c.vertical_path)
        except Exception as e:
            url = ""
            errors.append(f"url pública: {e}")
        job = PublishJob(Path(c.vertical_path), url, caption, verdict.title)
        for p in self.publishers:
            if p.name == "instagram" and not url:
                continue
            try:
                r = await p.publish(job)
                self.db.record_publication(c.id, r.platform, r.post_id, r.url, now)
                results.append(r)
            except Exception as e:
                errors.append(f"{p.name}: {e}")
        if results:
            c.state = State.PUBLISHED
            if not self.db.kv_get("first_post_day"):
                self.db.kv_set("first_post_day", local_day(now).isoformat())
        else:
            c.state = State.FAILED
            c.reason = "; ".join(errors)[:500] or "nenhuma plataforma configurada"
        self.db.update(c)
        await self.notifier.post_published(c, verdict, results, errors)

    async def _dry_run(self, c: Candidate, verdict: Verdict, caption: str,
                       now: datetime) -> None:
        preview = Path(c.vertical_path).with_name("preview.mp4")
        try:
            await self.preview_fn(Path(c.vertical_path), preview)
        except Exception as e:
            log.warning("prévia falhou: %s", e)
            preview = Path(c.vertical_path)
        self.db.record_publication(c.id, "dry_run", "", "", now)
        c.state = State.PUBLISHED
        self.db.update(c)
        await self.notifier.dry_run_preview(c, verdict, caption, preview)

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("scheduler: %s", e)
            await asyncio.sleep(30)
