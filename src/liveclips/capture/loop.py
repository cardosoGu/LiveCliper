"""Loop genérico de coleta periódica: erros são logados, nunca derrubam o app."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Awaitable, Callable

from ..core.timeutil import utcnow

log = logging.getLogger(__name__)


async def collector_loop(fn: Callable[[datetime], Awaitable[int]], interval_s: float,
                         name: str = "collector") -> None:
    while True:
        try:
            n = await fn(utcnow())
            if n:
                log.info("%s: %d novos candidatos", name, n)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("%s falhou: %s", name, e)
        await asyncio.sleep(interval_s)
