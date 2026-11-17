"""/testar: força um clipe agora e mostra o resultado de todo o fluxo, sem publicar.

Cria o clipe na live escolhida (ou na mais ativa do chat), passa pelo pipeline real
(download, legenda, vertical, IA) e devolve a prévia com a nota e o motivo, mesmo
quando a IA reprovaria. O candidato termina em `tested`, então o scheduler nunca o
publica.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from ..ai.verdict import Verdict
from ..capture.intake import candidate_from_twitch_clip
from ..capture.twitch.api import TwitchAPI, TwitchError
from ..capture.twitch.chat import ChannelInfo, ChatMonitor
from ..capture.twitch.clipper import wait_for_clip
from ..core.credits import credit_text
from ..core.db import DB
from ..core.models import State
from ..processing.ffmpeg import make_preview
from ..processing.pipeline import Pipeline
from ..publishing.caption import build_caption

log = logging.getLogger(__name__)

OUTCOMES = {
    State.QUEUED: "✅ SERIA PUBLICADO",
    State.REJECTED_AI: "❌ seria rejeitado",
    State.DETECTED: "⚠️ IA indisponível (nenhum modelo respondeu)",
    State.FAILED: "💥 falhou no processamento",
}


@dataclass
class TestResult:
    text: str
    preview_path: Path | None = None


class ManualTester:
    def __init__(self, api: TwitchAPI, db: DB, pipeline: Pipeline, chat: ChatMonitor,
                 preview_fn: Callable[[Path, Path], Awaitable[Path]] = make_preview,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.api = api
        self.db = db
        self.pipeline = pipeline
        self.chat = chat
        self.preview_fn = preview_fn
        self.sleep = sleep
        self._lock = asyncio.Lock()

    async def pick_channel(self, wanted: str | None) -> ChannelInfo | None:
        if wanted:
            login = wanted.strip().lstrip("#").lower()
            if login in self.chat.channels:
                return self.chat.channels[login]
            user = await self.api.get_user(login)
            return ChannelInfo(user["login"], user["id"], 100) if user else None
        active = self.chat.most_active()
        if active:
            return self.chat.channels[active[0]]
        if self.chat.channels:
            return max(self.chat.channels.values(), key=lambda i: i.viewers)
        return None

    async def run(self, wanted: str | None = None) -> TestResult:
        if self._lock.locked():
            return TestResult("⏳ Já tem um teste rodando, espere ele terminar.")
        async with self._lock:
            return await self._run(wanted)

    async def _run(self, wanted: str | None) -> TestResult:
        info = await self.pick_channel(wanted)
        if info is None:
            return TestResult(f"Canal não encontrado: {wanted}" if wanted else
                              "Nenhuma live monitorada ainda; espere a descoberta (~1 min).")
        try:
            clip_id = await self.api.create_clip(info.broadcaster_id)
        except TwitchError as e:
            hint = {400: "a live está offline ou não aceita clipes",
                    403: "o canal não permite clipes da conta bot",
                    401: "o token do bot é inválido"}.get(e.status, "erro da Twitch")
            return TestResult(f"🧪 Não consegui criar o clipe em {info.login}: {hint} "
                              f"({e.status}).")
        clip = await wait_for_clip(self.api, clip_id, self.sleep)
        if not clip:
            return TestResult(f"🧪 Clipe criado em {info.login}, mas a Twitch não o "
                              "liberou em 30 s. Tente de novo.")

        c = candidate_from_twitch_clip(clip, "manual_test", info.viewers)
        c.state = State.PROCESSING
        self.db.insert_candidate(c)
        await self.pipeline.process(c)
        c = self.db.get(c.id)
        outcome = OUTCOMES.get(c.state, c.state.value)

        lines = [f"🧪 **TESTE (nada foi publicado)** · {credit_text(c.platform, c.channel)}",
                 f"Clipe: {clip['url']}"]
        if c.ai_json:
            verdict = Verdict.model_validate_json(c.ai_json)
            lines += [f"Nota da IA: **{verdict.score:.1f}** (mínimo {self.pipeline.min_score()})"
                      f" · seguro: {'sim' if verdict.safe else 'NÃO'}",
                      f"Resultado: {outcome}",
                      f"Motivo: {verdict.reason}",
                      "", build_caption(verdict, c.platform, c.channel)]
        else:
            lines += [f"Resultado: {outcome}", f"Detalhe: {c.reason or '-'}"]

        preview = None
        if c.vertical_path and Path(c.vertical_path).exists():
            preview = Path(c.vertical_path).with_name("preview.mp4")
            try:
                await self.preview_fn(Path(c.vertical_path), preview)
            except Exception as e:
                log.warning("prévia do teste falhou: %s", e)
                preview = Path(c.vertical_path)

        c.state = State.TESTED
        c.reason = f"teste manual: {outcome}"
        self.db.update(c)
        return TestResult("\n".join(lines), preview)
