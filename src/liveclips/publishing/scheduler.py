"""Loop de publicação: publica, nas plataformas ligadas, os clipes que o admin aprovou."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from ..ai.verdict import Verdict
from ..core.config import Settings
from ..core.db import DB
from ..control.runtime import RuntimeSettings
from ..core.models import Candidate, State
from ..core.timeutil import local_day, local_midnight_utc, utcnow
from ..core.credits import credit_text
from ..processing.ffmpeg import render_final
from .caption import build_caption
from .platforms.base import PublishJob
from .policy import can_post_now

log = logging.getLogger(__name__)


class Notifier(Protocol):
    async def post_published(self, c, verdict, results, errors) -> None: ...
    async def alert(self, text: str) -> None: ...


# Quando NENHUMA plataforma aceita o post (token vencido, túnel fora), o clipe volta
# para a fila e o scheduler espera antes de tentar de novo: 5, 10, 20, 40, 60 min.
RETRY_BASE_MIN = 5
RETRY_MAX_MIN = 60
MAX_ATTEMPTS_PER_CLIP = 3
SERIES_GAP_S = 120  # pausa entre as partes de uma série, da última para a primeira


class Scheduler:
    def __init__(self, db: DB, cfg: Settings, publishers: list, media, notifier: Notifier,
                 runtime: RuntimeSettings, now_fn: Callable[[], datetime] = utcnow,
                 render_fn: Callable[..., Awaitable[Path]] = render_final,
                 on_status: Callable[[Candidate], Awaitable[None]] | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.sleep = sleep
        self.db = db
        self.cfg = cfg
        self.publishers = publishers
        self.media = media
        self.notifier = notifier
        self.runtime = runtime
        self.now_fn = now_fn
        self.render_fn = render_fn
        self.on_status = on_status
        self.fail_streak = 0
        self.retry_at: datetime | None = None
        self.attempts: dict[int, int] = {}

    def enabled_publishers(self) -> list:
        return [p for p in self.publishers if self.runtime.platform_enabled(p.name)]

    async def tick(self) -> bool:
        if self.runtime.paused() or not self.enabled_publishers():
            return False  # pausado ou tudo desligado: os aprovados esperam
        now = self.now_fn()
        if self.retry_at is not None and now < self.retry_at:
            return False
        approved = [c for c in self.db.by_state(State.APPROVED, 200) if self._series_ready(c)]
        if not approved:
            return False
        limit = self.runtime.post_max_per_day()
        posts_today = self.db.publications_count_since(local_midnight_utc(now))
        if posts_today >= limit:
            return False
        urgent = [c for c in approved if c.priority > 0]
        if urgent:  # "⚡ Agora": ignora o intervalo, não o limite diário
            c = min(urgent, key=lambda x: x.id)
        else:
            last = self.db.last_publication()
            if not can_post_now(now, last[0] if last else None, posts_today, limit,
                                self.runtime.post_interval_min()):
                return False
            c = min(approved, key=lambda x: x.id)  # ordem de aprovação
        if c.series_id:
            await self.publish_series(c, now)
        else:
            await self.publish(c, now)
        return True

    def _series_ready(self, c: Candidate) -> bool:
        """Série só sai quando nenhuma parte ainda espera aprovação/processamento."""
        if not c.series_id:
            return True
        done = (State.APPROVED, State.PUBLISHED, State.FAILED)
        return all(p.state in done for p in self.db.series_parts(c.series_id))

    async def publish_series(self, c: Candidate, now: datetime) -> None:
        """Um post só: partes aprovadas da última para a primeira, com pausa entre elas.
        Falha total numa parte não impede as outras."""
        parts = [p for p in self.db.series_parts(c.series_id) if p.state is State.APPROVED]
        for i, p in enumerate(sorted(parts, key=lambda x: x.part or 0, reverse=True)):
            if i:
                await self.sleep(SERIES_GAP_S)
            await self.publish(p, now)

    async def publish(self, c: Candidate, now: datetime) -> None:
        verdict = Verdict.model_validate_json(c.ai_json)
        caption = self._caption(c, verdict)
        if not await self.ensure_rendered(c, verdict):
            await self._notify_status(c)
            return
        publishers = self.enabled_publishers()
        results, errors = [], []
        url = ""
        if any(getattr(p, "needs_public_url", False) for p in publishers):
            try:
                url = await self.media.expose(c.vertical_path)
            except Exception as e:
                errors.append(f"url pública: {e}")
        job = PublishJob(Path(c.vertical_path), url, caption, verdict.title)
        for p in publishers:
            if getattr(p, "needs_public_url", False) and not url:
                continue
            try:
                r = await p.publish(job)
                self.db.record_publication(c.id, r.platform, r.post_id, r.url, now)
                results.append(r)
            except Exception as e:
                errors.append(f"{p.name}: {e}")
        if results:
            c.reason = "; ".join(errors)[:500] or None  # falhas parciais: botão 🔁 no card
            self._on_success(c, now)
            await self.notifier.post_published(c, verdict, results, errors)
        else:
            await self._on_total_failure(c, now, errors)
        await self._notify_status(c)

    @staticmethod
    def _caption(c: Candidate, verdict: Verdict) -> str:
        if c.series_id:
            return build_caption(verdict, c.platform, c.channel, part=c.part, parts=c.parts)
        return build_caption(verdict, c.platform, c.channel)

    async def publish_to(self, c: Candidate, platform: str) -> str:
        """🔁 do card: reenvia a uma plataforma só (ignora intervalo, limite e liga/desliga)."""
        pub = next((p for p in self.publishers if p.name == platform), None)
        if pub is None:
            return f"{platform} não configurado no .env."
        verdict = Verdict.model_validate_json(c.ai_json)
        state, reason = c.state, c.reason
        if not await self.ensure_rendered(c, verdict):
            why = c.reason
            c.state, c.reason = state, reason  # não rebaixa o clipe já publicado
            self.db.update(c)
            return f"💥 vídeo indisponível para reenviar: {why}"
        now = self.now_fn()
        try:
            url = (await self.media.expose(c.vertical_path)
                   if getattr(pub, "needs_public_url", False) else "")
            r = await pub.publish(PublishJob(Path(c.vertical_path), url,
                                             self._caption(c, verdict), verdict.title))
        except Exception as e:
            others = [e2 for e2 in (c.reason or "").split("; ")
                      if e2 and not e2.startswith(f"{platform}:")]
            c.reason = "; ".join([*others, f"{platform}: {e}"])[:500]
            self.db.update(c)
            await self._notify_status(c)
            return f"❌ {platform}: {e}"[:500]
        self.db.record_publication(c.id, r.platform, r.post_id, r.url, now)
        rest = [e2 for e2 in (c.reason or "").split("; ")
                if e2 and not e2.startswith(f"{platform}:")]
        c.reason = "; ".join(rest) or None
        self.db.update(c)
        await self._notify_status(c)
        return f"🔁 {platform}: enviado {r.url or r.post_id}"

    async def _notify_status(self, c: Candidate) -> None:
        if self.on_status is None:
            return
        try:
            await self.on_status(c)
        except Exception as e:  # o card do Discord não pode travar a publicação
            log.warning("status do clipe %s não atualizado: %s", c.id, e)

    def _on_success(self, c: Candidate, now: datetime) -> None:
        self.fail_streak = 0
        self.retry_at = None
        self.attempts.pop(c.id, None)
        c.state = State.PUBLISHED
        c.priority = 0
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

    async def ensure_rendered(self, c: Candidate, verdict: Verdict) -> bool:
        """Render final só dos aprovados: tela dividida (webcam em cima, jogo embaixo),
        legenda na emenda e título-gancho animado. Falhando, o clipe vai para `failed`."""
        if (c.vertical_path and Path(c.vertical_path).name == "final.mp4"
                and Path(c.vertical_path).exists()):
            return True  # já renderizado (nova tentativa ou card já postado)
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
                raw.with_name("subs.ass"), verdict.title, verdict.facecam,
                series=(c.part, c.parts) if c.series_id else None,
                layout=verdict.layout, subject=verdict.subject))
        except Exception as e:
            log.warning("render final falhou em %s: %s", c.clip_id, e)
            c.state, c.reason = State.FAILED, f"render final: {e}"[:500]
            self.db.update(c)
            await self.notifier.alert(f"Render final falhou em {c.clip_id}: {str(e)[:300]}")
            return False
        self.db.update(c)
        return True

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("scheduler: %s", e)
            await asyncio.sleep(30)
