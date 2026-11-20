"""Orquestra a avaliação em vídeo pelo Gemini (tenta cada modelo com cada chave)."""
from __future__ import annotations

import logging
from functools import partial
from pathlib import Path
from typing import Awaitable, Callable

from ..core.config import Settings
from ..core.db import DB
from .gemini import gemini_evaluate
from .prompt import build_prompt
from .taste import taste_block
from .verdict import Verdict, parse_verdict

log = logging.getLogger(__name__)

GeminiFn = Callable[[Path, str], Awaitable[str]]


class AIUnavailable(Exception):
    pass


class Evaluator:
    def __init__(self, cfg: Settings, db: DB, gemini_fn: GeminiFn | None = None):
        self.cfg = cfg
        self.db = db
        self.gemini_fn = gemini_fn or partial(gemini_evaluate, cfg)

    async def evaluate(self, video_path: Path, meta: dict) -> Verdict:
        if not (self.cfg.gemini_api_keys or self.cfg.gemini_api_key):
            raise AIUnavailable("nenhuma chave do Gemini configurada")
        prompt = build_prompt(meta, taste_block(self.db))
        try:
            return parse_verdict(await self.gemini_fn(video_path, prompt))
        except Exception as e:
            log.warning("Gemini falhou: %s", e)
            raise AIUnavailable(str(e)[:200]) from e
