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
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.api = api
        self.db = db
        self.bucket = bucket
        self.clock = clock
        self.sleep = sleep
        self.pending: list[PendingSpike] = []
        self.no_clip_until: dict[str, float] = {}

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
        await self._clip(best)

    async def _clip(self, p: PendingSpike) -> None:
        clip_id = None
        for attempt in range(3):
            try:
                clip_id = await self.api.create_clip(p.info.broadcaster_id)
                break
            except TwitchError as e:
                if e.status in (429, 500, 502, 503) and attempt < 2:
                    await self.sleep(2 ** attempt)
                    continue
                if e.status in (400, 403):
                    self.no_clip_until[p.info.login] = self.clock() + 3600
                log.warning("create_clip falhou em %s: %s", p.info.login, e)
                return
        if clip_id is None:
            return
        clip = None
        for _ in range(15):
            await self.sleep(2)
            clip = await self.api.get_clip(clip_id)
            if clip:
                break
        if not clip:
            log.warning("clipe %s não ficou disponível", clip_id)
            return
        c = candidate_from_twitch_clip(clip, "chat_spike", p.info.viewers, p.score)
        if admit(self.db, c):
            log.info("clipe criado: %s (%s, score %.1f)", c.clip_id, c.channel, p.score)

    async def run(self) -> None:
        while True:
            await self.tick()
            await self.sleep(1)
