"""Leva candidatos já preparados (corte + transcrição do sob demanda) de `detected` até
`awaiting_approval` (ou rejeitado) avaliando-os com a IA.

Aqui NÃO se baixa nem se renderiza: a IA avalia o vídeo original (e devolve onde está a
webcam), e o render final (tela dividida + título) só roda nos clipes aprovados.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..ai.evaluator import AIUnavailable, Evaluator
from ..core.config import Settings
from ..core.db import DB
from ..core.models import Candidate, State
from ..core.timeutil import local_day, utcnow

log = logging.getLogger(__name__)


@dataclass
class Pipeline:
    db: DB
    cfg: Settings
    evaluator: Evaluator
    media_dir: Path
    now_fn: Callable = utcnow
    concurrency: int = 2
    ai_down: bool = False
    _down_streak: int = 0

    def next_delay(self) -> float:
        """Segundos até o próximo tick: 5 s normalmente; com a IA fora do ar (cota
        esgotada, sobrecarga) 1, 2, 4, 8 e no máximo 15 min, para não gastar a cota
        tentando de novo a cada minuto."""
        if not self.ai_down:
            self._down_streak = 0
            return 5
        self._down_streak += 1
        return min(60 * 2 ** (self._down_streak - 1), 900)

    def _evals_key(self) -> str:
        return f"ai_evals:{local_day(self.now_fn()).isoformat()}"

    def evals_today(self) -> int:
        return int(float(self.db.kv_get(self._evals_key(), "0") or 0))

    def min_score(self) -> float:
        return float(self.db.kv_get("min_score", str(self.cfg.ai_min_score)))

    async def tick(self) -> int:
        # partes 2+ de uma série esperam: quem decide é a Parte 1, com a série inteira
        live = [c for c in self.db.by_state(State.DETECTED, 500) if c.part in (None, 1)]
        budget = self.cfg.ai_daily_budget - self.evals_today()
        batch = live[:max(0, min(self.concurrency, budget))]
        for c in batch:
            self._set(c, State.PROCESSING)
        self.ai_down = False
        await asyncio.gather(*(self.process(c) for c in batch))
        return len(batch)

    async def process(self, c: Candidate) -> None:
        group = [c]
        try:
            if c.series_id:
                group = [c if x.id == c.id else x for x in self.db.series_parts(c.series_id)]
            if not all(x.raw_path and Path(x.raw_path).exists() and x.transcript is not None
                       for x in group):
                # o corte e a transcrição vêm do sob demanda; nunca baixar nada aqui
                for x in group:
                    self._set(x, State.FAILED, "trecho sumiu antes da avaliação")
                return
            meta = {"platform": c.platform, "channel": c.channel, "viewers": c.viewers,
                    "source": c.source, "language": None,
                    "transcript": " … ".join(x.transcript for x in group)}
            if c.series_id:
                meta["parts"] = c.parts
            verdict = await self.evaluator.evaluate(Path(c.raw_path), Path(c.raw_path), meta)
        except AIUnavailable:
            self.ai_down = True
            self._set(c, State.DETECTED, "IA indisponível, aguardando")
            return
        except Exception as e:
            log.warning("candidato %s falhou: %s", c.id, e)
            for x in group:
                self._set(x, State.FAILED, str(e)[:500])
            return
        self.db.kv_incr_float(self._evals_key(), 1)
        if not verdict.safe:
            state, reason = State.REJECTED_AI, f"inseguro: {verdict.reason}"
        elif verdict.score < self.min_score():
            state, reason = State.REJECTED_AI, f"nota {verdict.score:.1f}: {verdict.reason}"
        else:
            state, reason = State.AWAITING_APPROVAL, verdict.reason
        for n, x in enumerate(group, 1):
            v = verdict if n == 1 else verdict.model_copy(
                update={"title": verdict.series_recap or f"PARTE {n}"})
            x.ai_score = verdict.score
            x.ai_json = v.model_dump_json()
            self._set(x, state, reason)
        if state is State.AWAITING_APPROVAL:
            log.info("aguardando aprovação: %s (%s, nota %.1f)",
                     c.clip_id, c.channel, verdict.score)

    def _set(self, c: Candidate, state: State, reason: str | None = None) -> None:
        c.state = state
        if reason is not None:
            c.reason = reason
        self.db.update(c)

    async def run(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self.next_delay())
