"""Fila do sob demanda: um vídeo por vez, em etapas que sobrevivem a um reinício."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol

import numpy as np

from ..ai.evaluator import AIUnavailable
from ..core.models import Candidate, State
from ..core.timeutil import parse_iso, utcnow
from ..processing.ffmpeg import probe_dimensions
from ..processing.subtitles import build_cues, cues_to_ass
from . import ingest, longtranscribe, signals
from .source import Source, SourceError, resolve_source
from .peaks import pick_peaks
from .series import SERIES_MAX, SINGLE_MAX, split_parts
from .snap import snap_clip
from .topics import Topic, choose_topics

log = logging.getLogger(__name__)

CUTTING_REASON = "recortando (sob demanda)"
DONE_STATES = {State.AWAITING_APPROVAL, State.APPROVED, State.PUBLISHED, State.DISCARDED}


class JobNotifier(Protocol):
    async def progress(self, job: dict, text: str) -> None: ...
    async def finished(self, job: dict, ok: bool, text: str) -> None: ...


class LogJobNotifier:
    async def progress(self, job, text):
        log.info("[sob demanda #%s] %s", job["id"], text)

    async def finished(self, job, ok, text):
        log.info("[sob demanda #%s] %s %s", job["id"], "✅" if ok else "❌", text)


@dataclass
class OnDemandOps:
    probe: object = ingest.probe
    download_audio: object = ingest.download_audio
    download_section: object = ingest.download_section
    fetch_chat: object = ingest.fetch_youtube_chat
    transcribe: object = None  # ligado no __init__ do runner (precisa da chave do Groq)
    rms: object = signals.rms_db_per_second
    topics: object = choose_topics
    probe_dims: object = probe_dimensions


class JobFailed(Exception):
    """Falha que vira ❌ com o motivo na thread."""


class OnDemandRunner:
    def __init__(self, db, cfg, http, jobs_dir: Path, media_dir: Path, notifier,
                 ops: OnDemandOps | None = None, now_fn=utcnow, ai_retry_s: float = 300, twitch=None):
        self.db = db
        self.cfg = cfg
        self.http = http
        self.jobs_dir = jobs_dir
        self.media_dir = media_dir
        self.notifier = notifier
        self.ops = ops or OnDemandOps()
        if self.ops.transcribe is None:
            async def _transcribe(audio, key, http, work):
                return await longtranscribe.transcribe_long(
                    audio, key, http, work, transcribe_fn=longtranscribe.groq_piece)
            self.ops.transcribe = _transcribe
        self.twitch = twitch
        self.now_fn = now_fn
        self.ai_retry_s = ai_retry_s

    # --- fila ---------------------------------------------------------------
    def enqueue(self, url: str, instructions: str, channel_id, message_id) -> int:
        return self.db.create_job(url, instructions[:500], channel_id, message_id)

    def queue_position(self, job_id: int) -> int:
        queued = [j["id"] for j in self.db.jobs_in(["queued"])]
        return queued.index(job_id) + 1 if job_id in queued else 0

    def status_line(self) -> str:
        running = self.db.jobs_in(["running"])
        waiting = len(self.db.jobs_in(["queued"]))
        if not running:
            held = len(self.db.jobs_in(["evaluating", "waiting_ai"]))
            if held and not waiting:
                return f"🔗 Sob demanda: {held} aguardando a IA"
            return "🔗 Sob demanda: livre" + (f" · {waiting} na fila" if waiting else "")
        j = running[0]
        return (f'🔗 Sob demanda: processando "{j["title"] or j["url"]}" '
                f'({j["progress"] or "começando"}) · {waiting} na fila')

    def requeue_waiting(self) -> None:
        limit = self.now_fn() - timedelta(seconds=self.ai_retry_s)
        for j in self.db.jobs_in(["waiting_ai"]):
            if parse_iso(j["updated_at"]) <= limit:
                self.db.update_job(j["id"], state="queued")

    # --- etapas -------------------------------------------------------------
    def _live_http(self, source) -> dict:
        """Live do YouTube baixa fragmentos por HTTP: passa o cliente compartilhado."""
        return {"http": self.http} if source.live else {}

    async def _progress(self, job: dict, step: str, text: str) -> None:
        self.db.update_job(job["id"], step=step, progress=text)
        job = self.db.get_job(job["id"])
        try:
            await self.notifier.progress(job, text)
        except Exception as e:
            log.warning("progresso do job %s não enviado: %s", job["id"], e)

    async def _finish(self, job: dict, ok: bool, text: str, state: str) -> None:
        self.db.update_job(job["id"], state=state, progress=text,
                           error=None if ok else text[:500])
        shutil.rmtree(self.jobs_dir / str(job["id"]), ignore_errors=True)
        try:
            await self.notifier.finished(self.db.get_job(job["id"]), ok, text)
        except Exception as e:
            log.warning("fim do job %s não enviado: %s", job["id"], e)

    async def process(self, job: dict) -> None:
        jid = job["id"]
        work = self.jobs_dir / str(jid)
        work.mkdir(parents=True, exist_ok=True)
        self.db.update_job(jid, state="running")
        try:
            meta, source = await self._meta(job, work)
            audio = await self._audio(job, work, meta, source)
            transcript = await self._transcript(job, work, audio)
            if not transcript.words:
                await self._finish(job, True, "nenhuma fala encontrada no vídeo", "done")
                return
            score, rms = await self._signals(job, work, meta, source, audio)
            topics = await self._topics(job, work, meta, transcript, score)
            made = await self._cuts(job, meta, source, transcript, topics, rms)
        except AIUnavailable:
            self.db.update_job(jid, state="waiting_ai", progress="aguardando a IA voltar…")
            await self._progress(self.db.get_job(jid), "topics", "⏸️ aguardando a IA voltar…")
            return
        except longtranscribe.TranscriptionBusy:
            self.db.update_job(jid, state="waiting_ai", progress="aguardando o Groq…")
            await self._progress(self.db.get_job(jid), "transcript",
                                 "⏸️ limite do Groq atingido, tento de novo em alguns minutos…")
            return
        except JobFailed as e:
            await self._finish(job, False, str(e), "failed")
            return
        except Exception as e:
            log.exception("job %s falhou", jid)
            msg = ingest.friendly_error(str(e)) or f"erro: {str(e)[:300]}"
            await self._finish(job, False, msg, "failed")
            return
        if made == 0:
            await self._finish(job, True, "nenhum momento forte encontrado neste vídeo", "done")
            return
        self.db.update_job(jid, state="evaluating")
        await self._progress(self.db.get_job(jid), "evaluating",
                             f"🧠 a IA está avaliando {made} corte(s)…")

    async def _meta(self, job, work):
        path = work / "meta.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if "meta" in data:
                return ingest.VideoMeta(**data["meta"]), Source(**data["source"])
            meta = ingest.VideoMeta(**data)  # formato antigo: vídeo gravado
            return meta, Source(meta.url, False, 0.0, meta.duration)
        await self._progress(job, "meta", "🔎 lendo o vídeo…")
        meta = await self.ops.probe(job["url"])
        try:
            source = await resolve_source(meta, self.now_fn().timestamp(),
                                          self.cfg.ondemand_max_hours * 3600, self.twitch)
        except SourceError as e:
            raise JobFailed(str(e)) from e
        path.write_text(json.dumps({"meta": asdict(meta), "source": asdict(source)},
                                   ensure_ascii=False), encoding="utf-8")
        self.db.update_job(job["id"], title=meta.title[:200])
        if source.note:
            await self._progress(job, "meta", source.note)
        return meta, source

    async def _audio(self, job, work, meta, source):
        path = work / "audio.mp3"
        if not path.exists():
            await self._progress(job, "audio", "⬇️ baixando o áudio…")
            windowed = meta.is_live or source.start > 0
            path = await self.ops.download_audio(
                source.url, path, start=source.start if windowed else None,
                end=source.end if windowed else None, live=source.live,
                **self._live_http(source))
        return path

    async def _transcript(self, job, work, audio):
        path = work / "transcript.json"
        if path.exists():
            return longtranscribe.load_transcript(path)
        await self._progress(job, "transcript", "📝 transcrevendo…")
        t = await self.ops.transcribe(audio, self.cfg.groq_api_key, self.http, work)
        longtranscribe.dump_transcript(t, path)
        return t

    async def _signals(self, job, work, meta, source, audio):
        score_path, rms_path = work / "score.npy", work / "rms.npy"
        if score_path.exists() and rms_path.exists():
            return np.load(score_path), np.load(rms_path)
        await self._progress(job, "signals",
                             "📈 procurando os picos (volume, mais repetidos, chat)…")
        rms = await self.ops.rms(audio, work)
        n = max(rms.size, int(source.end - source.start))
        # chat e heatmap são do vídeo inteiro e só existem em gravações
        chat = (await self.ops.fetch_chat(job["url"], work)
                if meta.platform == "youtube" and not meta.is_live else [])
        whole = source.start == 0 and not source.live
        score = signals.combine({
            "loudness": signals.loudness_signal(rms) if rms.size else None,
            "heatmap": signals.heatmap_signal(meta.heatmap, n) if whole else None,
            "chat": signals.chat_signal(chat, n),
        })
        np.save(score_path, score)
        np.save(rms_path, rms)
        return score, rms

    async def _topics(self, job, work, meta, transcript, score) -> list[Topic]:
        path = work / "topics.json"
        if path.exists():
            return [Topic(**t) for t in json.loads(path.read_text(encoding="utf-8"))]
        await self._progress(job, "topics", "🤖 a IA está escolhendo os momentos…")
        peaks = pick_peaks(score)
        topics = await self.ops.topics(self.cfg, meta.title, transcript.words, peaks,
                                       job["instructions"], db=self.db)
        path.write_text(json.dumps([asdict(t) for t in topics]), encoding="utf-8")
        return topics

    async def _cuts(self, job, meta, source, transcript, topics, rms) -> int:
        existing = {c.clip_id: c for c in self.db.job_candidates(job["id"])}
        clips = []  # (início, fim, series_id, parte, partes), tempos relativos à fonte
        for t in topics:
            whole = snap_clip(transcript.words, t.peak, t.start, t.end, rms, max_len=SERIES_MAX)
            if whole is None:
                continue
            parts = split_parts(transcript.words, *whole) if whole[1] - whole[0] > SINGLE_MAX else None
            if parts is None and whole[1] - whole[0] > SINGLE_MAX:
                single = snap_clip(transcript.words, t.peak, t.start, t.end, rms)
                if single is None:
                    continue
                parts = [single]
            parts = parts or [whole]
            if len(parts) == 1:
                clips.append((*parts[0], None, None, None))
            else:
                sid = f"{job['id']}:{int((source.start + whole[0]) * 10)}"
                clips.extend((a, b, sid, n, len(parts)) for n, (a, b) in enumerate(parts, 1))
        # 1) cria TODAS as linhas antes de cortar (o pipeline nunca vê meia série)
        todo = []  # (i, cand, s, e, sid) ainda por cortar
        made = 0
        blocked = set()  # séries com linha que não deu para criar
        for i, (s, e, sid, part, parts) in enumerate(clips, 1):
            clip_id = f"ondemand:{job['id']}:{int((source.start + s) * 10)}"
            cand = existing.get(clip_id)
            if cand is not None and not (cand.state is State.FAILED
                                         and cand.reason == CUTTING_REASON):
                made += 1  # já cortado (pronto ou além)
                continue
            if cand is None:
                cand = self._new_candidate(job, meta, source, clip_id, s, e)
                cand.series_id, cand.part, cand.parts = sid, part, parts
                if self.db.insert_candidate(cand) is None:
                    if sid:
                        blocked.add(sid)
                    continue  # já existia
            todo.append((i, cand, s, e, sid))
        # 2) corta; clipe avulso é liberado na hora, série só quando todas as partes cortaram
        series_ok: dict[str, list[Candidate]] = {}
        for i, cand, s, e, sid in todo:
            if sid and sid in blocked:
                continue
            await self._progress(job, "cuts", f"✂️ recortando {i}/{len(clips)}…")
            try:
                await self._finish_cut(job, source, transcript, cand, s, e,
                                       release=sid is None)
                if sid is None:
                    made += 1
                else:
                    series_ok.setdefault(sid, []).append(cand)
            except Exception as ex:
                log.warning("corte %s do job %s falhou: %s", i, job["id"], ex)
                if sid:
                    blocked.add(sid)
        for sid, cands in series_ok.items():
            if sid in blocked:
                continue  # série incompleta: fica FAILED/recortando e um novo run tenta de novo
            for cand in cands:
                cand.state, cand.reason = State.DETECTED, None
                self.db.update(cand)
            made += len(cands)
        if made == 0 and clips:
            raise JobFailed("não consegui recortar nenhum trecho (erro no download)")
        return made

    def _new_candidate(self, job, meta, source, clip_id, s, e) -> Candidate:
        platform, channel = meta.platform, meta.credit_hint or meta.channel
        if platform == "youtube" and not meta.credit_hint:
            platform, channel = "outro", "youtube.com"  # sem handle: o crédito não inventa um
        return Candidate(platform=platform,
                         source="ondemand", clip_id=clip_id, clip_url=job["url"],
                         channel=channel, created_at=self.now_fn(), duration=e - s,
                         title=meta.title, rank=1000.0, state=State.FAILED,
                         reason=CUTTING_REASON, job_id=job["id"], start_s=source.start + s,
                         end_s=source.start + e)

    async def _finish_cut(self, job, source, transcript, c, s, e, release: bool = True) -> None:
        """Baixa o trecho, gera legenda/transcrição e (se `release`) libera para o pipeline."""
        folder = self.media_dir / str(c.id)
        raw = await self.ops.download_section(
            source.url, source.start + s, source.start + e, folder / "raw.mp4", live=source.live,
            **self._live_http(source))
        words = [{**w, "start": w["start"] - s, "end": w["end"] - s}
                 for w in transcript.words if w["start"] >= s and w["end"] <= e]
        cues = build_cues(words)
        if cues:
            w, h = await self.ops.probe_dims(raw)
            (folder / "subs.ass").write_text(cues_to_ass(cues, w, h), encoding="utf-8")
        c.raw_path = str(raw)
        c.transcript = " ".join(x["word"].strip() for x in words)
        if release:
            c.state, c.reason = State.DETECTED, None
        self.db.update(c)

    async def check_evaluating(self) -> None:
        for job in self.db.jobs_in(["evaluating"]):
            cands = self.db.job_candidates(job["id"])
            if any(c.state in (State.DETECTED, State.PROCESSING) for c in cands):
                continue
            sent = [c for c in cands if c.state in DONE_STATES]
            failed = [c for c in cands if c.state is State.FAILED]
            rejected = sorted((c for c in cands if c.state is State.REJECTED_AI),
                              key=lambda c: c.ai_score or 0, reverse=True)
            text = (f"{len(sent)} corte(s) enviado(s) para o #aprovação, "
                    f"{len(rejected)} descartado(s) pela IA")
            if failed:
                text += f", {len(failed)} falharam"
            if not sent and not rejected:
                await self._finish(job, False,
                                   f"nenhum corte ficou pronto ({len(failed)} falharam)", "failed")
                continue
            if not sent and rejected:
                text = "nenhum corte passou da nota mínima. Os melhores descartados:\n" + \
                    "\n".join(f"• {c.reason}" for c in rejected[:3])
                if failed:
                    text += f"\n({len(failed)} falharam)"
            await self._finish(job, True, text, "done")

    async def run(self) -> None:
        self.db.reset_running_jobs()
        while True:
            try:
                self.requeue_waiting()
                await self.check_evaluating()
                job = self.db.next_job()
                if job is not None:
                    await self.process(job)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("fila do sob demanda")
            await asyncio.sleep(5)
