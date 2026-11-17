"""Lógica do painel de controle (avisos, comandos, botões), independente do Discord."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from pathlib import Path
from typing import Callable, Protocol

from ..core.config import Settings
from ..core.db import DB
from ..core.credits import credit_text
from ..core.models import State
from ..core.timeutil import local_day, local_midnight_utc, utcnow

log = logging.getLogger(__name__)

HELP = ("Comandos: /pause · /resume · /status · /block canal · /unblock canal · "
        "/min_score N")
OUTBOX_MAX = 200


class Transport(Protocol):
    async def send(self, text: str, buttons: list[tuple[str, str]] | None = None) -> None: ...
    async def send_file(self, path: Path, text: str) -> None: ...


class LogTransport:
    """Usado quando o Discord não está configurado: só escreve no log."""

    async def send(self, text, buttons=None):
        log.info("[aviso] %s", text)

    async def send_file(self, path, text):
        log.info("[arquivo %s] %s", path, text)


class Monitor:
    def __init__(self, db: DB, transport: Transport, publishers_by_name: dict, cfg: Settings,
                 channel_count: Callable[[], int] = lambda: 0, now_fn=utcnow,
                 clock: Callable[[], float] = time.monotonic):
        self.db = db
        self.transport = transport
        self.publishers = publishers_by_name
        self.cfg = cfg
        self.channel_count = channel_count
        self.now_fn = now_fn
        self.clock = clock
        self._last_alert: dict[str, float] = {}
        # Avisos vão para uma fila drenada por `run_sender`: quem avisa (scheduler,
        # supervisor) nunca espera o Discord, mesmo se ele estiver fora do ar.
        self._outbox: asyncio.Queue[tuple] = asyncio.Queue(maxsize=OUTBOX_MAX)

    def _enqueue(self, item: tuple) -> None:
        if self._outbox.full():
            self._outbox.get_nowait()  # descarta o mais antigo
            log.warning("fila de avisos cheia: aviso mais antigo descartado")
        self._outbox.put_nowait(item)

    async def _deliver(self, item: tuple) -> None:
        kind = item[0]
        try:
            if kind == "file":
                _, path, text = item
                try:
                    await self.transport.send_file(Path(path), text)
                except Exception as e:
                    log.warning("falha ao enviar prévia: %s", e)
                    await self.transport.send(text + f"\n(prévia não enviada: {e})", None)
            else:
                _, text, buttons = item
                await self.transport.send(text, buttons)
        except Exception as e:
            log.warning("falha ao enviar aviso: %s", e)

    async def run_sender(self) -> None:
        while True:
            await self._deliver(await self._outbox.get())

    async def flush(self) -> None:
        """Entrega tudo o que está na fila agora (testes e encerramento)."""
        while not self._outbox.empty():
            await self._deliver(self._outbox.get_nowait())

    async def _send(self, text: str, buttons=None) -> None:
        self._enqueue(("text", text, buttons))

    async def info(self, text: str) -> None:
        await self._send(text)

    async def alert(self, text: str) -> None:
        now = self.clock()
        if now - self._last_alert.get(text, -1e9) < 600:
            return
        self._last_alert[text] = now
        await self._send(f"⚠️ {text}")

    async def post_published(self, c, verdict, results, errors) -> None:
        if results:
            lines = [f"✅ **Publicado** — nota {c.ai_score:.1f} · {credit_text(c.platform, c.channel)}",
                     verdict.title]
            lines += [f"• {r.platform}: {r.url or r.post_id}" for r in results]
        else:
            lines = [f"❌ **Falhou ao publicar** · {credit_text(c.platform, c.channel)}"]
        if errors:
            lines.append("Falhas: " + "; ".join(errors))
        buttons = [("🗑️ Apagar post", f"del:{c.id}"), ("🚫 Bloquear canal", f"block:{c.channel}")]
        await self._send("\n".join(lines), buttons if results else None)

    async def dry_run_preview(self, c, verdict, caption, preview_path) -> None:
        text = (f"🧪 **ENSAIO (nada foi publicado)** — nota {c.ai_score:.1f} · "
                f"{credit_text(c.platform, c.channel)}\n{verdict.reason}\n\n{caption}")
        self._enqueue(("file", str(preview_path), text))

    def status_text(self) -> str:
        now = self.now_fn()
        day = local_day(now).isoformat()
        counts = self.db.counts_since(now - timedelta(hours=1))
        last_hour = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nada"
        paused = self.db.kv_get("paused") == "1"
        min_score = self.db.kv_get("min_score", str(self.cfg.ai_min_score))
        spend = float(self.db.kv_get(f"claude_spend:{day}", "0") or 0)
        evals = int(float(self.db.kv_get(f"ai_evals:{day}", "0") or 0))
        return "\n".join([
            f"**Status:** {'⏸️ pausado' if paused else '▶️ ativo'}"
            + (" · 🧪 modo ensaio" if self.cfg.dry_run else ""),
            f"Lives monitoradas: {self.channel_count()}",
            f"Última hora: {last_hour}",
            f"Posts hoje: {self.db.publications_count_since(local_midnight_utc(now))}",
            f"Avaliações de IA hoje: {evals}/{self.cfg.ai_daily_budget}",
            f"Gasto Claude hoje: US$ {spend:.2f} / {self.cfg.claude_daily_budget_usd:.2f}",
            f"Nota mínima: {min_score}",
            f"Canais bloqueados: {len(self.db.blocked_channels())}",
        ])

    def handle_command(self, name: str, arg: str | None = None) -> str:
        name = name.lstrip("/").lower()
        arg = (arg or "").strip() or None
        if name == "pause":
            self.db.kv_set("paused", "1")
            return "⏸️ Publicações pausadas. Use /resume para retomar."
        if name == "resume":
            self.db.kv_set("paused", "0")
            return "▶️ Publicações retomadas."
        if name == "status":
            return self.status_text()
        if name in ("block", "unblock"):
            if not arg:
                return f"Uso: /{name} nome_do_canal"
            if name == "block":
                self.db.block(arg, "discord")
                return f"🚫 {arg.lower()} bloqueado."
            self.db.unblock(arg)
            return f"✅ {arg.lower()} desbloqueado."
        if name == "min_score":
            try:
                value = float((arg or "").replace(",", "."))
            except ValueError:
                return "Uso: /min_score N (0 a 10)"
            if not 0 <= value <= 10:
                return "A nota precisa estar entre 0 e 10."
            self.db.kv_set("min_score", str(value))
            return f"🎯 Nota mínima agora: {value}"
        return HELP

    async def handle_action(self, custom_id: str) -> str:
        kind, _, value = custom_id.partition(":")
        if kind == "block" and value:
            self.db.block(value, "discord")
            return f"🚫 {value} bloqueado."
        if kind == "del" and value.isdigit():
            c = self.db.get(int(value))
            if c is None:
                return "Clipe não encontrado."
            msgs = []
            for pub in self.db.publications_for(c.id):
                where = pub["url"] or pub["post_id"] or "(sem link)"
                p = self.publishers.get(pub["platform"])
                if p is not None and hasattr(p, "delete") and pub["post_id"]:
                    ok = await p.delete(pub["post_id"])
                    msgs.append(f"{pub['platform']}: " + ("apagado ✅" if ok
                                else f"falhou, apague manualmente: {where}"))
                elif pub["platform"] != "dry_run":
                    msgs.append(f"{pub['platform']}: apague manualmente: {where}")
            c.state = State.DELETED
            c.reason = "apagado pelo Discord"
            self.db.update(c)
            return "🗑️ " + ("\n".join(msgs) if msgs else "Marcado como apagado.")
        return "Ação desconhecida."
