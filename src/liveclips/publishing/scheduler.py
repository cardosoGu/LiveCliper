"""Loop de publicação: aplica a política e publica em cada plataforma (ou ensaio)."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from ..ai.verdict import Verdict
from ..core.config import Settings
from ..core.db import DB
from ..core.models import Candidate, State, is_expired
from ..core.timeutil import local_day, local_midnight_utc, utcnow
from ..core.credits import credit_text
from ..processing.ffmpeg import make_preview, render_final
from .caption import build_caption
from .platforms.base import PublishJob
from .policy import can_post_now, daily_limit, pick_next

log = logging.getLogger(__name__)


class Notifier(Protocol):
    async def post_published(self, c, verdict, results, errors) -> None: ...
    async def dry_run_preview(self, c, verdict, caption, preview_path) -> None: ...
    async def alert(self, text: str) -> None: ...


# Quando NENHUMA plataforma aceita o post (token vencido, túnel fora), o clipe volta
# para a fila e o scheduler espera antes de tentar de novo: 5, 10, 20, 40, 60 min.
RETRY_BASE_MIN = 5
RETRY_MAX_MIN = 60
MAX_ATTEMPTS_PER_CLIP = 3


class Scheduler:
    def __init__(self, db: DB, cfg: Settings, publishers: list, media, notifier: Notifier,
                 now_fn: Callable[[], datetime] = utcnow,
                 preview_fn: Callable[[Path, Path], Awaitable[Path]] = make_preview,
                 render_fn: Callable[..., Awaitable[Path]] = render_final):
        self.db = db
        self.cfg = cfg
        self.publishers = publishers
        self.media = media
        self.notifier = notifier
        self.now_fn = now_fn
        self.preview_fn = preview_fn
        self.render_fn = render_fn
        self.fail_streak = 0
        self.retry_at: datetime | None = None
        self.attempts: dict[int, int] = {}

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
        if self.retry_at is not None and now < self.retry_at:
            return False
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
        if not await self._render_final(c, verdict):
            return
        if self.cfg.dry_run:
            await self._dry_run(c, verdict, caption, now)
            return
        results, errors = [], []
        url = ""
        if any(getattr(p, "needs_public_url", False) for p in self.publishers):
            try:
                url = await self.media.expose(c.vertical_path)
            except Exception as e:
                errors.append(f"url pública: {e}")
        job = PublishJob(Path(c.vertical_path), url, caption, verdict.title)
        for p in self.publishers:
            if getattr(p, "needs_public_url", False) and not url:
                continue
            try:
                r = await p.publish(job)
                self.db.record_publication(c.id, r.platform, r.post_id, r.url, now)
                results.append(r)
            except Exception as e:
                errors.append(f"{p.name}: {e}")
        if results:
            self._on_success(c, now)
            await self.notifier.post_published(c, verdict, results, errors)
        else:
            await self._on_total_failure(c, now, errors)

    def _on_success(self, c: Candidate, now: datetime) -> None:
        self.fail_streak = 0
        self.retry_at = None
        self.attempts.pop(c.id, None)
        c.state = State.PUBLISHED
        if not self.db.kv_get("first_post_day"):
            self.db.kv_set("first_post_day", local_day(now).isoformat())
        self.db.update(c)

    async def _on_total_failure(self, c: Candidate, now: datetime, errors: list[str]) -> None:
        """Nada foi publicado: o clipe NÃO é descartado (provável problema de
        token/túnel, não do clipe); só desiste dele após algumas tentativas."""
        reason = "; ".join(errors)[:500] or "nenhuma plataforma configurada"
        self.attempts[c.id] = self.attempts.get(c.id, 0) + 1
        if self.attempts[c.id] >= MAX_ATTEMPTS_PER_CLIP:
            c.state = State.FAILED
            self.attempts.pop(c.id, None)
        c.reason = reason
        self.db.update(c)
        self.fail_streak += 1
        wait = min(RETRY_BASE_MIN * 2 ** (self.fail_streak - 1), RETRY_MAX_MIN)
        self.retry_at = now + timedelta(minutes=wait)
        await self.notifier.alert(
            f"Nenhuma plataforma aceitou o post. Publicações em espera; "
            f"tentando de novo em {wait} min. Erro: {reason}")

    async def _render_final(self, c: Candidate, verdict: Verdict) -> bool:
        """Render final só dos aprovados: tela dividida (webcam em cima, jogo embaixo),
        legenda na emenda e título-gancho animado. Falhando, o clipe vai para `failed`."""
        if c.vertical_path and Path(c.vertical_path).name == "final.mp4"                 and Path(c.vertical_path).exists():
            return True  # já renderizado (nova tentativa de publicação)
        raw = Path(c.raw_path) if c.raw_path else None
        if raw is None or not raw.exists():
            if c.vertical_path and Path(c.vertical_path).exists():
                return True  # clipe antigo, renderizado antes da tela dividida
            c.state, c.reason = State.FAILED, "vídeo original sumiu antes do render"
            self.db.update(c)
            return False
        try:
            c.vertical_path = str(await self.render_fn(
                raw, raw.with_name("final.mp4"), credit_text(c.platform, c.channel),
                raw.with_name("subs.ass"), verdict.title, verdict.facecam))
        except Exception as e:
            log.warning("render final falhou em %s: %s", c.clip_id, e)
            c.state, c.reason = State.FAILED, f"render final: {e}"[:500]
            self.db.update(c)
            await self.notifier.alert(f"Render final falhou em {c.clip_id}: {str(e)[:300]}")
            return False
        self.db.update(c)
        return True

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
