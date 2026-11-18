"""Liga e desliga o automático (motor da V1: lives + picos no chat) conforme o painel."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from ..core.supervisor import supervise
from .runtime import RuntimeSettings

log = logging.getLogger(__name__)

Jobs = dict[str, Callable[[], Awaitable[None]]]


class AutoController:
    def __init__(self, runtime: RuntimeSettings,
                 resolve: Callable[[list[str]], Awaitable[dict[str, str]]],
                 make_jobs: Callable[[list[str], int], Jobs],
                 alert: Callable[[str], Awaitable[None]],
                 on_stop: Callable[[], None] = lambda: None):
        self.runtime = runtime
        self.resolve = resolve
        self.make_jobs = make_jobs
        self.alert = alert
        self.on_stop = on_stop
        self.found: dict[str, str] = {}
        self.missing: list[str] = []
        self._tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return bool(self._tasks)

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if tasks:
            self.on_stop()

    async def apply(self) -> str:
        """Aplica o que está no painel: para tudo e, se ligado, sobe de novo."""
        async with self._lock:  # dois cliques seguidos não sobem tarefas em dobro
            await self.stop()
            if not self.runtime.auto_on():
                return "⚪ Automático desligado."
            wanted = self.runtime.categories()
            found = await self.resolve(wanted)
            self.found = found
            self.missing = [n for n in wanted if n.lower() not in {f.lower() for f in found}]
            if not found:
                return "⚠️ Nenhuma categoria encontrada na Twitch; o automático não foi ligado."
            jobs = self.make_jobs(list(found.values()), self.runtime.min_viewers())
            self._tasks = [asyncio.create_task(supervise(name, factory, self.alert))
                           for name, factory in jobs.items()]
            log.info("automático ligado: %s (mín. %d espectadores)",
                     ", ".join(found), self.runtime.min_viewers())
            msg = (f"🟢 Automático ligado · {', '.join(found)} · "
                   f"mín. {self.runtime.min_viewers()} espectadores")
            if self.missing:
                msg += f" · não encontradas na Twitch: {', '.join(self.missing)}"
            return msg

    def status_line(self) -> str:
        if not self.running:
            return "📡 Automático: DESLIGADO"
        return f"📡 Automático: LIGADO · {len(self.found)} categorias"
