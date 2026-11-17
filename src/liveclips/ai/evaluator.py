"""Orquestra a avaliação: Gemini → Claude (com teto diário) → indisponível."""
from __future__ import annotations

import logging
from functools import partial
from pathlib import Path
from typing import Awaitable, Callable

from ..core.config import Settings
from ..core.db import DB
from ..core.timeutil import local_day, utcnow
from .claude import claude_evaluate
from .gemini import gemini_evaluate
from .prompt import build_prompt
from .verdict import Verdict, parse_verdict

log = logging.getLogger(__name__)

GeminiFn = Callable[[Path, str], Awaitable[str]]
ClaudeFn = Callable[[Path, str], Awaitable[tuple[str, float]]]


class AIUnavailable(Exception):
    pass


class Evaluator:
    def __init__(self, cfg: Settings, db: DB, gemini_fn: GeminiFn | None = None,
                 claude_fn: ClaudeFn | None = None, now_fn=utcnow):
        self.cfg = cfg
        self.db = db
        self.gemini_fn = gemini_fn or partial(gemini_evaluate, cfg)
        self.claude_fn = claude_fn or partial(claude_evaluate, cfg)
        self.now_fn = now_fn

    def _spend_key(self) -> str:
        return f"claude_spend:{local_day(self.now_fn()).isoformat()}"

    def claude_spent_today(self) -> float:
        return float(self.db.kv_get(self._spend_key(), "0") or 0)

    async def evaluate(self, video_path: Path, raw_path: Path, meta: dict) -> Verdict:
        prompt = build_prompt(meta)
        if self.cfg.gemini_api_keys or self.cfg.gemini_api_key:
            try:
                return parse_verdict(await self.gemini_fn(video_path, prompt))
            except Exception as e:
                log.warning("Gemini falhou: %s", e)
        if self.cfg.anthropic_api_key:
            if self.claude_spent_today() >= self.cfg.claude_daily_budget_usd:
                log.warning("teto diário do Claude atingido")
            else:
                try:
                    text, cost = await self.claude_fn(raw_path, prompt)
                    self.db.kv_incr_float(self._spend_key(), cost)
                    return parse_verdict(text)
                except Exception as e:
                    log.warning("Claude falhou: %s", e)
        raise AIUnavailable("nenhuma IA disponível")
