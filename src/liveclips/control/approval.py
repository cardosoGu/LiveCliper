"""#aprovação: cada clipe aprovado pela IA vira um card com botões; só o que o admin
aprova vai para a fila de publicação. Independente do Discord (o transporte é injetado)."""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from ..ai.verdict import Verdict
from ..core.db import DB
from ..core.models import Candidate, State
from ..core.timeutil import utcnow
from ..ondemand.topics import fmt_time
from .runtime import RuntimeSettings

log = logging.getLogger(__name__)

ACTIONS = ("approve", "now", "edit", "discard", "retry-instagram", "retry-youtube", "retry-tiktok")
PLATFORM_LABEL = {"instagram": "Reels", "youtube": "Shorts", "tiktok": "TikTok"}


def approval_custom_id(action: str, candidate_id: int) -> str:
    return f"appr:{action}:{candidate_id}"


def parse_custom_id(custom_id: str) -> tuple[str, int] | None:
    parts = custom_id.split(":")
    if (len(parts) != 3 or parts[0] != "appr" or parts[1] not in ACTIONS
            or not parts[2].isdigit()):
        return None
    return parts[1], int(parts[2])


class ApprovalTransport(Protocol):
    async def post(self, text: str, video: Path | None, link: str | None,
                   candidate_id: int, buttons: bool) -> int: ...

    async def edit(self, message_id: int, candidate_id: int, text: str,
                   buttons: bool, retry: tuple[str, ...] = ()) -> None: ...


class LogApprovalTransport:
    """Sem Discord configurado: os cards só aparecem no log."""

    async def post(self, text, video, link, candidate_id, buttons):
        log.info("[aprovação #%s] %s", candidate_id, text)
        return 0

    async def edit(self, message_id, candidate_id, text, buttons, retry=()):
        log.info("[aprovação #%s] %s", candidate_id, text)


class ApprovalDesk:
    def __init__(self, db: DB, runtime: RuntimeSettings, transport: ApprovalTransport,
                 render: Callable[[Candidate, Verdict], Awaitable[bool]],
                 shrink: Callable[[Path, Path], Awaitable[Path]],
                 expose: Callable[[str], Awaitable[str]],
                 platform_names: list[str], upload_limit_mb: float = 10.0,
                 now_fn: Callable = utcnow,
                 retry: Callable[[Candidate, str], Awaitable[str]] | None = None):
        self.db = db
        self.runtime = runtime
        self.transport = transport
        self.render = render
        self.shrink = shrink
        self.expose = expose
        self.platform_names = platform_names
        self.limit_bytes = int(upload_limit_mb * 1024 * 1024)
        self.now_fn = now_fn
        self.retry = retry
        self._retrying: set[tuple[int, str]] = set()

    # --- cards ------------------------------------------------------------
    async def tick(self) -> int:
        posted = 0
        for c in self.db.awaiting_cards(posted=False, limit=100):
            try:
                if c.series_id:
                    posted += await self._post_series(c)
                elif await self._post(c):
                    posted += 1
            except Exception as e:  # um card quebrado não trava a fila inteira
                log.warning("card do clipe %s falhou: %s", c.id, e)
        return posted

    async def _post(self, c: Candidate) -> bool:
        verdict = Verdict.model_validate_json(c.ai_json)
        if not await self.render(c, verdict):
            return False  # o render marcou o clipe como failed e avisou no #logs
        video, link = await self._attachment(Path(c.vertical_path))
        no_video = video is None and link is None
        c.approval_message_id = await self.transport.post(
            self.card_text(c, verdict, no_video), video, link, c.id, True)
        self.db.update(c)
        return True

    async def _post_series(self, c: Candidate) -> int:
        """Série = um card só: botões na Parte 1, as demais logo abaixo sem botões."""
        c = self.db.get(c.id) or c  # a lista do tick pode estar velha (a série acabou de ser postada)
        if c.approval_message_id is not None or c.state is not State.AWAITING_APPROVAL:
            return 0
        parts = self.db.series_parts(c.series_id)
        first = parts[0] if parts else c
        if c.part != 1:
            # parte órfã (o post falhou depois do card da Parte 1): só repõe ela mesma
            if first.approval_message_id is None or first.state is not State.AWAITING_APPROVAL:
                return 0
            return int(await self._post_part(c, False))
        if c.id != first.id or len(parts) < (c.parts or 1):
            return 0
        if any(p.state is not State.AWAITING_APPROVAL or p.approval_message_id is not None
               for p in parts):
            return 0
        for p in parts:
            if not await self.render(p, Verdict.model_validate_json(p.ai_json)):
                for q in self.db.series_parts(c.series_id):  # a série toda falha junto
                    if q.state is not State.FAILED:
                        q.state, q.reason = State.FAILED, "parte da série falhou ao renderizar"
                        self.db.update(q)
                return 0
        n = 0
        for p in parts:
            n += int(await self._post_part(p, p.part == 1, render=False))
        return n

    async def _post_part(self, c: Candidate, buttons: bool, render: bool = True) -> bool:
        verdict = Verdict.model_validate_json(c.ai_json)
        if render and not await self.render(c, verdict):
            return False
        video, link = await self._attachment(Path(c.vertical_path))
        no_video = video is None and link is None
        c.approval_message_id = await self.transport.post(
            self.card_text(c, verdict, no_video), video, link, c.id, buttons)
        self.db.update(c)
        return True

    async def _attachment(self, path: Path) -> tuple[Path | None, str | None]:
        """Arquivo se couber no limite do Discord; senão uma versão leve; senão um link."""
        if path.stat().st_size <= self.limit_bytes:
            return path, None
        small = path.with_name("discord.mp4")
        try:
            await self.shrink(path, small)
            if small.stat().st_size <= self.limit_bytes:
                return small, None
        except Exception as e:
            log.warning("recompressão para o Discord falhou: %s", e)
        try:
            return None, await self.expose(str(path))
        except Exception as e:
            log.warning("link público falhou: %s", e)
            return None, None

    def failed_platforms(self, c: Candidate) -> list[str]:
        if c.state is not State.PUBLISHED or not c.reason:
            return []
        return [n for n in self.platform_names if f"{n}:" in c.reason]

    def card_text(self, c: Candidate, verdict: Verdict, no_video: bool = False) -> str:
        if c.series_id and c.part != 1:
            return f"🎞️ Parte {c.part}/{c.parts} — {verdict.title}"
        targets = ", ".join(PLATFORM_LABEL.get(n, n) for n in self.platform_names
                            if self.runtime.platform_enabled(n)) or "nenhuma (todas desligadas)"
        origin = f"🔗 {c.title[:80]} · {fmt_time(c.start_s or 0)}"
        lines = [f"🎬 **{verdict.title}** — nota {verdict.score:.1f}",
                 f"_{verdict.reason}_", origin, "",
                 verdict.caption, " ".join(verdict.hashtags), "",
                 f"Vai para: {targets}"]
        if c.series_id:
            lines.append(f"🎞️ Série em {c.parts} partes — os botões valem para todas")
        if no_video:
            lines.append("⚠️ vídeo indisponível no Discord")
        return "\n".join(lines)

    # --- ações ------------------------------------------------------------
    async def handle(self, action: str, candidate_id: int, note: str = "") -> str:
        c = self.db.get(candidate_id)
        if c is None:
            return "Clipe não encontrado."
        if action.startswith("retry-"):
            platform = action.removeprefix("retry-")
            if c.state is not State.PUBLISHED or self.retry is None:
                return "Esse clipe não foi publicado ainda."
            if platform not in self.failed_platforms(c):
                return "Essa plataforma não está com falha."
            key = (c.id, platform)
            if key in self._retrying:
                return "Reenvio já em andamento."
            self._retrying.add(key)
            try:
                return await self.retry(c, platform)
            finally:
                self._retrying.discard(key)
        if c.state is not State.AWAITING_APPROVAL:
            return f"Esse clipe já foi decidido ({c.state.value})."
        group = self._group(c)
        note = " ".join((note or "").split())[:500]
        if action in ("approve", "now", "discard"):
            self._learn(group[0], "descartado" if action == "discard" else "aprovado", note)
        if action in ("approve", "now"):
            for p in group:
                p.state = State.APPROVED
                p.priority = 1 if action == "now" else 0
                p.reason = None  # daqui em diante, reason guarda falhas de publicação
                self.db.update(p)
            for p in group:
                await self.on_status(p)
            return ("⚡ Aprovado: publica agora (respeitando o limite diário)."
                    if action == "now" else "✅ Aprovado: entrou na fila.")
        if action == "discard":
            for p in group:
                p.state = State.DISCARDED
                p.reason = "descartado no Discord" + (f": {note}" if note else "")
                self._delete_media(p)
                p.raw_path = p.vertical_path = None
                self.db.update(p)
            for p in group:
                await self.on_status(p)
            return "❌ Descartado."
        return "Ação desconhecida."

    def _learn(self, c: Candidate, decision: str, note: str) -> None:
        """Guarda a decisão como exemplo para a IA (a série conta uma vez, pela Parte 1)."""
        try:
            v = Verdict.model_validate_json(c.ai_json)
            parts = self._group(c) if c.series_id else [c]
            transcript = " … ".join(p.transcript or "" for p in parts)
            self.db.record_feedback(c.id, decision, v.title, c.ai_score, v.reason,
                                    transcript, note)
        except Exception as e:  # aprender nunca pode travar a decisão
            log.warning("guardar a decisão do clipe %s falhou: %s", c.id, e)

    def _group(self, c: Candidate) -> list[Candidate]:
        """O clipe sozinho, ou todas as partes da série que ainda aguardam aprovação."""
        if not c.series_id:
            return [c]
        return [p for p in self.db.series_parts(c.series_id)
                if p.state is State.AWAITING_APPROVAL] or [c]

    async def edit(self, candidate_id: int, title: str, caption: str) -> str:
        c = self.db.get(candidate_id)
        if c is None or c.state is not State.AWAITING_APPROVAL:
            return "Esse clipe não está mais aguardando aprovação."
        title, caption = title.strip(), caption.strip()
        if not title:
            return "O título não pode ficar vazio."
        olds: list[tuple[int, int]] = []
        for p in self._group(c):
            update = {"caption": caption}
            if p.part in (None, 1):
                update["title"] = title  # o título-gancho é só da Parte 1
            verdict = Verdict.model_validate_json(p.ai_json).model_copy(update=update)
            p.ai_json = verdict.model_dump_json()
            if p.vertical_path and Path(p.vertical_path).name == "final.mp4":
                Path(p.vertical_path).unlink(missing_ok=True)  # o título está queimado no vídeo
            p.vertical_path = None
            old_message = p.approval_message_id
            p.approval_message_id = None  # o próximo tick refaz o vídeo e posta um card novo
            self.db.update(p)
            if old_message is not None:
                olds.append((old_message, p.id))
        for message_id, pid in olds:  # só depois de gravar tudo: falha do Discord não trava a série
            try:
                await self.transport.edit(message_id, pid,
                                          "✏️ Editado — a nova versão chega em seguida.", False)
            except Exception as e:
                log.warning("marcar o card do clipe %s como editado falhou: %s", pid, e)
        return "✏️ Salvo. Refazendo o vídeo com o novo título."

    def _delete_media(self, c: Candidate) -> None:
        for p in (c.vertical_path, c.raw_path):
            if p and Path(p).parent.name == str(c.id):  # só a pasta do próprio clipe
                shutil.rmtree(Path(p).parent, ignore_errors=True)
                return

    # --- status depois da decisão -----------------------------------------
    async def on_status(self, c: Candidate) -> None:
        c = self.db.get(c.id) or c
        if not c.approval_message_id:
            return
        verdict = Verdict.model_validate_json(c.ai_json)
        text = self.card_text(c, verdict) + "\n\n" + self.status_line(c)
        await self.transport.edit(c.approval_message_id, c.id, text,
                                  c.state is State.AWAITING_APPROVAL and c.part in (None, 1),
                                  tuple(self.failed_platforms(c)))

    def status_line(self, c: Candidate) -> str:
        if c.state is State.APPROVED:
            line = ("⚡ aprovado — publicando agora" if c.priority
                    else "✅ aprovado — na fila de publicação")
            if c.reason:
                line += f" · última tentativa falhou: {c.reason}"
            return line
        if c.state is State.PUBLISHED:
            parts = []
            for p in self.db.publications_for(c.id):
                label = PLATFORM_LABEL.get(p["platform"], p["platform"])
                if p["url"]:
                    parts.append(f"{label} ✅ {p['url']}")
                elif p["platform"] == "tiktok":
                    parts.append(f"{label} 📥 rascunho/privado (abra o app do TikTok)")
                else:
                    parts.append(f"{label} 🔒 enviado (privado)")
            for name in self.failed_platforms(c):
                err = next((e for e in c.reason.split("; ") if e.startswith(f"{name}:")), "")
                parts.append(f"{PLATFORM_LABEL.get(name, name)} ❌ {err.removeprefix(f'{name}:').strip()}")
            line = "📤 publicado — " + " · ".join(parts)
            if c.series_id and c.part == 1 and any(
                    p["platform"] == "tiktok" and not p["url"]
                    for p in self.db.publications_for(c.id)):
                line += f"\n📌 TikTok: publique as partes do app na ordem {c.parts} → 1"
            return line
        if c.state is State.DISCARDED:
            why = (c.reason or "").removeprefix("descartado no Discord").removeprefix(": ")
            return "❌ descartado" + (f" — {why}" if why else "")
        if c.state is State.FAILED:
            return f"💥 falhou: {c.reason}"
        return ""

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("aprovação: %s", e)
            await asyncio.sleep(15)
