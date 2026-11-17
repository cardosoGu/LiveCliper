"""Mantém cada módulo vivo: se cair, alerta e reinicia com espera crescente."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

log = logging.getLogger(__name__)


async def supervise(name: str, factory: Callable[[], Awaitable[None]],
                    alert: Callable[[str], Awaitable[None]],
                    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                    backoff_max: float = 300, max_restarts: int | None = None) -> None:
    delay = 5.0
    restarts = 0
    while max_restarts is None or restarts <= max_restarts:
        try:
            await factory()
            log.warning("%s terminou; reiniciando", name)
            delay = 5.0
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("%s caiu", name)
            await alert(f"{name} caiu: {type(e).__name__}: {e}"[:500])
        restarts += 1
        await sleep(delay)
        delay = min(delay * 2, backoff_max)
