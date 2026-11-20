from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .timeutil import utcnow


class State(StrEnum):
    DETECTED = "detected"
    PROCESSING = "processing"
    QUEUED = "queued"
    PUBLISHED = "published"
    REJECTED_RANK = "rejected_rank"  # pode voltar a detected se o clipe ganhar views
    DUPLICATE = "duplicate"  # mesmo momento de outro candidato mais forte
    REJECTED_AI = "rejected_ai"
    EXPIRED = "expired"
    DELETED = "deleted"
    FAILED = "failed"
    TESTED = "tested"  # legado do /testar (removido); mantido para linhas antigas do banco
    AWAITING_APPROVAL = "awaiting_approval"  # IA aprovou; card no #aprovação
    APPROVED = "approved"  # admin aprovou; fila de publicação
    DISCARDED = "discarded"  # admin descartou


@dataclass
class Candidate:
    platform: str  # "youtube" | "twitch" | "kick" | "outro"
    source: str  # "ondemand" (linhas antigas: "chat_spike", "twitch_public", …)
    clip_id: str  # único, com prefixo da plataforma
    clip_url: str
    channel: str
    created_at: datetime
    duration: float = 30.0
    title: str = ""
    # viewers, view_count, spike_score, rank e extra_clip_url vêm do clipador em tempo real
    # (removido); continuam aqui só porque as colunas existem no banco.
    viewers: int = 0
    view_count: int = 0
    spike_score: float = 0.0
    rank: float = 0.0
    state: State = State.DETECTED
    id: int | None = None
    raw_path: str | None = None
    vertical_path: str | None = None
    extra_clip_url: str | None = None  # continuação do lance (~30 s depois)
    transcript: str | None = None
    ai_score: float | None = None
    ai_json: str | None = None
    reason: str | None = None
    updated_at: datetime = field(default_factory=utcnow)
    approval_message_id: int | None = None  # mensagem do card no #aprovação
    priority: int = 0  # 1 = "⚡ Agora": fura o intervalo entre posts
    job_id: int | None = None  # pedido sob demanda que gerou o corte
    start_s: float | None = None  # posição do corte no vídeo original
    end_s: float | None = None
    series_id: str | None = None  # partes de um mesmo assunto compartilham o id
    part: int | None = None  # 1, 2, 3…
    parts: int | None = None  # total de partes da série
