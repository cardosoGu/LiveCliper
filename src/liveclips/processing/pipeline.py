"""Leva candidatos de `detected` até `queued` (ou rejeitado): mídia, legenda e IA.

Aqui NÃO se renderiza o vertical: a IA avalia o vídeo original (e devolve onde está a
webcam), e o render final (tela dividida + título) só roda nos clipes aprovados.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from ..ai.evaluator import AIUnavailable, Evaluator
from ..core.config import Settings
from ..core.db import DB
from ..core.models import Candidate, State, is_expired
from ..core.timeutil import local_day, utcnow
from . import transcribe as tr
from .download import download_clip
from .ffmpeg import concat_clips, extract_audio, probe_dimensions
from .subtitles import build_cues, cues_to_ass, words_from_segments

log = logging.getLogger(__name__)


@dataclass
class MediaOps:
    download: Callable[[str, Path], Awaitable[Path]] = download_clip
    extract_audio: Callable[[Path, Path], Awaitable[Path]] = extract_audio
    transcribe: Callable[..., Awaitable[tr.Transcript]] = tr.transcribe
    probe: Callable[[Path], Awaitable[tuple[int, int]]] = probe_dimensions
    concat: Callable[[list[Path], Path], Awaitable[Path]] = concat_clips


@dataclass
class Pipeline:
    db: DB
    cfg: Settings
    evaluator: Evaluator
    http: httpx.AsyncClient | None
    media_dir: Path
    ops: MediaOps = field(default_factory=MediaOps)
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
        now = self.now_fn()
        detected = self.db.by_state(State.DETECTED, 500)
        live = []
        for c in detected:
            if c.source != "ondemand" and is_expired(c, now, self.cfg.queue_ttl_min):
                self._set(c, State.EXPIRED, "passou do prazo antes de processar")
            elif c.rank < self.cfg.rank_threshold:
                self._set(c, State.REJECTED_RANK, f"rank {c.rank:.1f} abaixo do limiar")
            else:
                live.append(c)
        budget = self.cfg.ai_daily_budget - self.evals_today()
        batch = live[:max(0, min(self.concurrency, budget))]
        for c in batch:
            self._set(c, State.PROCESSING)
        self.ai_down = False
        await asyncio.gather(*(self.process(c) for c in batch))
        return len(batch)

    async def process(self, c: Candidate) -> None:
        work = self.media_dir / str(c.id)
        language = None
        try:
            if not (c.raw_path and Path(c.raw_path).exists() and c.transcript is not None):
                if c.source == "ondemand":
                    # clip_url é o vídeo inteiro: nunca baixar de novo como se fosse o clipe
                    self._set(c, State.FAILED, "trecho sumiu antes da avaliação")
                    return
                transcript = await self._prepare_media(c, work)
                if transcript is None:
                    return  # rejeitado antes da IA (fala em outro idioma)
                language = transcript.language
            meta = {"platform": c.platform, "channel": c.channel, "viewers": c.viewers,
                    "source": c.source, "transcript": c.transcript or "",
                    "language": language}
            verdict = await self.evaluator.evaluate(Path(c.raw_path), Path(c.raw_path), meta)
        except AIUnavailable:
            self.ai_down = True
            self._set(c, State.DETECTED, "IA indisponível, aguardando")
            return
        except Exception as e:
            log.warning("candidato %s falhou: %s", c.id, e)
            self._set(c, State.FAILED, str(e)[:500])
            return
        self.db.kv_incr_float(self._evals_key(), 1)
        c.ai_score = verdict.score
        c.ai_json = verdict.model_dump_json()
        if not verdict.safe:
            self._set(c, State.REJECTED_AI, f"inseguro: {verdict.reason}")
        elif verdict.score < self.min_score():
            self._set(c, State.REJECTED_AI, f"nota {verdict.score:.1f}: {verdict.reason}")
        elif c.source == "manual_test":
            self._set(c, State.TESTED, verdict.reason)  # /testar nunca vira card
        else:
            self._set(c, State.AWAITING_APPROVAL, verdict.reason)
            log.info("aguardando aprovação: %s (%s, nota %.1f)",
                     c.clip_id, c.channel, verdict.score)

    async def _prepare_media(self, c: Candidate, work: Path) -> tr.Transcript | None:
        """Baixa, transcreve e grava a legenda (subs.ass). Retorna None se o clipe foi rejeitado
        por idioma (economiza render e orçamento de IA)."""
        work.mkdir(parents=True, exist_ok=True)
        raw = await self.ops.download(c.clip_url, work / "raw.mp4")
        if c.extra_clip_url:  # continuação do lance: vídeo de ~60 s
            try:
                extra = await self.ops.download(c.extra_clip_url, work / "raw2.mp4")
                raw = await self.ops.concat([raw, extra], work / "combined.mp4")
            except Exception as e:
                log.warning("continuação de %s ignorada: %s", c.clip_id, e)
        c.raw_path = str(raw)
        audio = await self.ops.extract_audio(raw, work / "audio.mp3")
        transcript = await self.ops.transcribe(audio, self.cfg.groq_api_key, self.http)
        c.transcript = transcript.text
        if transcript.is_clearly_not_portuguese():
            self._set(c, State.REJECTED_AI,
                      f"fala detectada em '{transcript.language}', não em português")
            return None
        words = transcript.words or words_from_segments(transcript.segments)
        cues = build_cues(words)
        if cues:
            src_w, src_h = await self.ops.probe(raw)
            (work / "subs.ass").write_text(cues_to_ass(cues, src_w, src_h), encoding="utf-8")
        self.db.update(c)
        return transcript

    def _set(self, c: Candidate, state: State, reason: str | None = None) -> None:
        c.state = state
        if reason is not None:
            c.reason = reason
        self.db.update(c)

    async def run(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self.next_delay())
