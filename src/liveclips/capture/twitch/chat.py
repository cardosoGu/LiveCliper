"""Leitura anônima do chat da Twitch via IRC e detecção de picos por canal."""
from __future__ import annotations

import asyncio
import collections
import logging
import random
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..spike import Spike, SpikeDetector

log = logging.getLogger(__name__)


@dataclass
class ChannelInfo:
    login: str
    broadcaster_id: str
    viewers: int


def parse_privmsg(line: str) -> tuple[str, str] | None:
    """':nick!nick@nick.tmi.twitch.tv PRIVMSG #canal :mensagem' -> ('canal', 'mensagem')."""
    if " PRIVMSG #" not in line:
        return None
    try:
        _, rest = line.split(" PRIVMSG #", 1)
        channel, text = rest.split(" :", 1)
    except ValueError:
        return None
    return channel.strip().lower(), text.rstrip("\r\n")


class ChatMonitor:
    def __init__(self, on_spike: Callable[[ChannelInfo, Spike], Awaitable[None]],
                 detector_factory: Callable[[], SpikeDetector],
                 host: str = "irc.chat.twitch.tv", port: int = 6697,
                 clock: Callable[[], float] = time.time):
        self.on_spike = on_spike
        self.detector_factory = detector_factory
        self.host = host
        self.port = port
        self.clock = clock
        self.channels: dict[str, ChannelInfo] = {}
        self.detectors: dict[str, SpikeDetector] = {}
        self._joined: set[str] = set()
        # estatísticas para log e /status (janela que reinicia a cada relatório)
        self._msgs: collections.Counter[str] = collections.Counter()
        self._stats_since = self.clock()
        self.spikes_total = 0

    def summary(self, now: float | None = None) -> str:
        now = self.clock() if now is None else now
        minutes = max((now - self._stats_since) / 60, 1 / 60)
        total = sum(self._msgs.values())
        top = ", ".join(f"{ch} {n / minutes:.0f}/min" for ch, n in self._msgs.most_common(3))
        return (f"chat: {len(self._joined)}/{len(self.channels)} canais, "
                f"{total / minutes:.0f} msgs/min no total"
                + (f" (mais ativos: {top})" if top else "")
                + f", picos detectados desde o início: {self.spikes_total}")

    def _report(self, now: float, every_s: float = 300) -> None:
        if now - self._stats_since >= every_s:
            log.info(self.summary(now))
            self._msgs.clear()
            self._stats_since = now

    def set_channels(self, infos: list[ChannelInfo]) -> None:
        self.channels = {i.login.lower(): i for i in infos}
        for login in self.channels:
            self.detectors.setdefault(login, self.detector_factory())
        for login in list(self.detectors):
            if login not in self.channels:
                del self.detectors[login]

    def handle_line(self, line: str, now: float) -> str | None:
        if line.startswith("PING"):
            return "PONG" + line[4:]
        parsed = parse_privmsg(line)
        if parsed:
            channel, text = parsed
            det = self.detectors.get(channel)
            if det is not None:
                det.add_message(now, text)
                self._msgs[channel] += 1
        return None

    async def check_spikes(self, now: float) -> None:
        for login, det in list(self.detectors.items()):
            spike = det.poll(now)
            if spike is not None and login in self.channels:
                self.spikes_total += 1
                log.info("pico em %s: %d msgs em 10 s (%.1fx o normal)",
                         login, spike.msgs, spike.ratio)
                await self.on_spike(self.channels[login], spike)
        self._report(now)

    async def run(self) -> None:
        while True:
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # reconecta sempre
                log.warning("chat desconectado: %s", e)
            self._joined.clear()
            await asyncio.sleep(5)

    async def _session(self) -> None:
        reader, writer = await asyncio.open_connection(self.host, self.port, ssl=True)
        nick = f"justinfan{random.randint(10000, 99999)}"
        writer.write(f"PASS SCHMOOPIIE\r\nNICK {nick}\r\n".encode())
        await writer.drain()
        tasks = [asyncio.create_task(self._joiner(writer)),
                 asyncio.create_task(self._ticker())]
        try:
            while True:
                raw = await asyncio.wait_for(reader.readline(), timeout=360)
                if not raw:
                    raise ConnectionError("conexão fechada pelo servidor")
                reply = self.handle_line(raw.decode("utf-8", errors="ignore"), self.clock())
                if reply:
                    writer.write((reply.rstrip("\r\n") + "\r\n").encode())
                    await writer.drain()
        finally:
            for t in tasks:
                t.cancel()
            writer.close()

    async def _joiner(self, writer: asyncio.StreamWriter) -> None:
        while True:
            wanted = set(self.channels)
            for login in self._joined - wanted:
                writer.write(f"PART #{login}\r\n".encode())
                self._joined.discard(login)
            pending = sorted(wanted - self._joined)
            for i in range(0, len(pending), 20):  # limite do IRC: 20 JOINs / 10 s
                for login in pending[i:i + 20]:
                    writer.write(f"JOIN #{login}\r\n".encode())
                    self._joined.add(login)
                await writer.drain()
                if i + 20 < len(pending):
                    await asyncio.sleep(11)
            await asyncio.sleep(5)

    async def _ticker(self) -> None:
        while True:
            await self.check_spikes(self.clock())
            await asyncio.sleep(1)
