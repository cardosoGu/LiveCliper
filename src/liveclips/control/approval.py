"""#aprovação: cada clipe aprovado pela IA vira um card com botões; só o que o admin
aprova vai para a fila de publicação. Independente do Discord (o transporte é injetado)."""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from ..ai.verdict import Verdict
from ..core.credits import credit_text
from ..core.db import DB
from ..core.models import Candidate, State
from ..core.timeutil import utcnow
from .runtime import RuntimeSettings

log = logging.getLogger(__name__)

ACTIONS = ("approve", "now", "edit", "discard", "retry-instagram", "retry-youtube", "retry-tiktok")
AGE_WARN_H = 2  # cards do automático mostram a idade a partir daqui
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
        self._age_shown: dict[int, int] = {}

    # --- cards ------------------------------------------------------------
    async def tick(self) -> int:
        posted = 0
        for c in self.db.awaiting_cards(posted=False, limit=100):
            if c.source == "manual_test":
                continue  # /testar nunca vira card
            try:
                if await self._post(c):
                    posted += 1
            except Exception as e:  # um card quebrado não trava a fila inteira
                log.warning("card do clipe %s falhou: %s", c.id, e)
        for c in self.db.awaiting_cards(posted=True, limit=50):
            try:
                await self._refresh_age(c)
            except Exception as e:
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
        self._age_shown[c.id] = self._age_hours(c)
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

    def _age_hours(self, c: Candidate) -> int:
        return int((self.now_fn() - c.created_at).total_seconds() // 3600)

    async def _refresh_age(self, c: Candidate) -> None:
        if c.source == "ondemand":
            return  # vídeo gravado não perde a graça com o tempo
        hours = self._age_hours(c)
        if hours < AGE_WARN_H or self._age_shown.get(c.id) == hours:
            return
        self._age_shown[c.id] = hours
        verdict = Verdict.model_validate_json(c.ai_json)
        await self.transport.edit(c.approval_message_id, c.id,
                                  self.card_text(c, verdict), True)

    def failed_platforms(self, c: Candidate) -> list[str]:
        if c.state is not State.PUBLISHED or not c.reason:
            return []
        return [n for n in self.platform_names if f"{n}:" in c.reason]

    def card_text(self, c: Candidate, verdict: Verdict, no_video: bool = False) -> str:
        targets = ", ".join(PLATFORM_LABEL.get(n, n) for n in self.platform_names
                            if self.runtime.platform_enabled(n)) or "nenhuma (todas desligadas)"
        origin = f"📡 Automático · {credit_text(c.platform, c.channel)}"
        if c.source == "chat_spike":
            origin += " · pico no chat"
        lines = [f"🎬 **{verdict.title}** — nota {verdict.score:.1f}",
                 f"_{verdict.reason}_", origin, "",
                 verdict.caption, " ".join(verdict.hashtags), "",
                 f"Vai para: {targets}"]
        hours = self._age_hours(c)
        if c.source != "ondemand" and hours >= AGE_WARN_H:
            lines.append(f"⏰ há {hours} h")
        if no_video:
            lines.append("⚠️ vídeo indisponível no Discord")
        return "\n".join(lines)

    # --- ações ------------------------------------------------------------
    async def handle(self, action: str, candidate_id: int) -> str:
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
        if action in ("approve", "now"):
            c.state = State.APPROVED
            c.priority = 1 if action == "now" else 0
            c.reason = None  # daqui em diante, reason guarda falhas de publicação
            self.db.update(c)
            await self.on_status(c)
            return ("⚡ Aprovado: publica agora (respeitando o limite diário)."
                    if action == "now" else "✅ Aprovado: entrou na fila.")
        if action == "discard":
            c.state = State.DISCARDED
            c.reason = "descartado no Discord"
            self._delete_media(c)
            c.raw_path = c.vertical_path = None
            self.db.update(c)
            await self.on_status(c)
            return "❌ Descartado."
        return "Ação desconhecida."

    async def edit(self, candidate_id: int, title: str, caption: str) -> str:
        c = self.db.get(candidate_id)
        if c is None or c.state is not State.AWAITING_APPROVAL:
            return "Esse clipe não está mais aguardando aprovação."
        title, caption = title.strip(), caption.strip()
        if not title:
            return "O título não pode ficar vazio."
        verdict = Verdict.model_validate_json(c.ai_json).model_copy(
            update={"title": title, "caption": caption})
        c.ai_json = verdict.model_dump_json()
        if c.vertical_path and Path(c.vertical_path).name == "final.mp4":
            Path(c.vertical_path).unlink(missing_ok=True)  # o título está queimado no vídeo
        c.vertical_path = None
        old_message = c.approval_message_id
        c.approval_message_id = None  # o próximo tick refaz o vídeo e posta um card novo
        self.db.update(c)
        if old_message is not None:
            await self.transport.edit(old_message, c.id,
                                      "✏️ Editado — a nova versão chega em seguida.", False)
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
                                  c.state is State.AWAITING_APPROVAL,
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
            return "📤 publicado — " + " · ".join(parts)
        if c.state is State.DISCARDED:
            return "❌ descartado"
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
