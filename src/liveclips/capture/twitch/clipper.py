"""Transforma picos de chat em clipes reais via Twitch Create Clip, com orçamento global."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from .chat import ChannelInfo
from ...core.db import DB
from ..intake import admit, candidate_from_twitch_clip
from ..spike import Spike, spike_score
from .api import TwitchAPI, TwitchError

log = logging.getLogger(__name__)


class TokenBucket:
    """Libera no máximo uma criação de clipe a cada `rate_seconds`."""

    def __init__(self, rate_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.rate = rate_seconds
        self.clock = clock
        self._next = 0.0

    def try_take(self) -> bool:
        now = self.clock()
        if now >= self._next:
            self._next = now + self.rate
            return True
        return False


@dataclass
class PendingSpike:
    info: ChannelInfo
    spike: Spike
    score: float
    received: float


async def wait_for_clip(api: TwitchAPI, clip_id: str,
                        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                        attempts: int = 15, interval_s: float = 2) -> dict | None:
    """A Twitch leva alguns segundos para processar um clipe recém-criado."""
    for _ in range(attempts):
        await sleep(interval_s)
        try:
            clip = await api.get_clip(clip_id)
        except TwitchError as e:
            log.warning("get_clip %s: %s", clip_id, e)
            continue
        if clip:
            return clip
    log.warning("clipe %s não ficou disponível", clip_id)
    return None


async def follow_up_clip(api: TwitchAPI, broadcaster_id: str, delay_s: float,
                         sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> str | None:
    """Clipe de continuação: os ~30 s seguintes ao primeiro, para mostrar o desfecho."""
    if delay_s <= 0:
        return None
    await sleep(delay_s)
    try:
        clip_id = await api.create_clip(broadcaster_id)
    except TwitchError as e:
        log.warning("clipe de continuação falhou: %s", e)
        return None
    clip = await wait_for_clip(api, clip_id, sleep)
    return clip["url"] if clip else None


def choose_spike(pending: list[PendingSpike], now: float,
                 max_age: float = 15.0) -> tuple[PendingSpike | None, list[PendingSpike]]:
    """O Create Clip pega os ~30 s anteriores; pico com mais de `max_age` s já passou."""
    fresh = [p for p in pending if now - p.received <= max_age]
    if not fresh:
        return None, []
    best = max(fresh, key=lambda p: p.score)
    return best, [p for p in fresh if p is not best]


class Clipper:
    def __init__(self, api: TwitchAPI, db: DB, bucket: TokenBucket,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 followup_delay_s: float = 0):
        self.api = api
        self.followup_delay_s = followup_delay_s
        self.db = db
        self.bucket = bucket
        self.clock = clock
        self.sleep = sleep
        self.pending: list[PendingSpike] = []
        self.no_clip_until: dict[str, float] = {}
        self._waiters: set[asyncio.Task] = set()

    async def on_spike(self, info: ChannelInfo, spike: Spike) -> None:
        if self.no_clip_until.get(info.login, 0.0) > self.clock():
            return
        score = spike_score(spike.ratio, spike.keyword_bonus, info.viewers)
        self.pending.append(PendingSpike(info, spike, score, self.clock()))

    async def tick(self) -> None:
        if not self.pending:
            return
        best, rest = choose_spike(self.pending, self.clock())
        if best is None:
            self.pending = []
            return
        if not self.bucket.try_take():
            self.pending = [best, *rest]
            return
        self.pending = rest
        clip_id = await self._create(best)
        if clip_id is not None:
            # Esperar o clipe ficar pronto leva até 30 s: roda em paralelo para
            # não deixar os próximos picos passarem do prazo de 15 s.
            task = asyncio.create_task(self._await_clip(clip_id, best))
            self._waiters.add(task)
            task.add_done_callback(self._waiters.discard)

    async def drain(self) -> None:
        """Espera as tarefas de espera de clipe em andamento (usado em testes)."""
        while self._waiters:
            await asyncio.gather(*list(self._waiters))

    async def _create(self, p: PendingSpike) -> str | None:
        for attempt in range(3):
            try:
                return await self.api.create_clip(p.info.broadcaster_id)
            except TwitchError as e:
                if e.status in (429, 500, 502, 503) and attempt < 2:
                    await self.sleep(2 ** attempt)
                    continue
                if e.status in (400, 403):
                    self.no_clip_until[p.info.login] = self.clock() + 3600
                log.warning("create_clip falhou em %s: %s", p.info.login, e)
                return None
        return None

    async def _await_clip(self, clip_id: str, p: PendingSpike) -> None:
        clip = await wait_for_clip(self.api, clip_id, self.sleep)
        if not clip:
            return
        c = candidate_from_twitch_clip(clip, "chat_spike", p.info.viewers, p.score)
        c.extra_clip_url = await follow_up_clip(self.api, p.info.broadcaster_id,
                                                self.followup_delay_s, self.sleep)
        if admit(self.db, c):
            log.info("clipe criado: %s (%s, score %.1f)", c.clip_id, c.channel, p.score)

    async def run(self) -> None:
        while True:
            await self.tick()
            await self.sleep(1)
