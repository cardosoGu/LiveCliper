"""Avaliação por IA: Gemini (vê o vídeo) → fallback Claude (8 frames) → indisponível."""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable

from pydantic import BaseModel, ValidationError

from .config import Settings
from .db import DB
from .media import extract_frames
from .timeutil import local_day, utcnow

log = logging.getLogger(__name__)


class Verdict(BaseModel):
    score: float
    safe: bool
    reason: str
    title: str
    caption: str
    hashtags: list[str]


VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number"},
        "safe": {"type": "boolean"},
        "reason": {"type": "string"},
        "title": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "safe", "reason", "title", "caption", "hashtags"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """Você é o editor-chefe de um perfil brasileiro de clipes de GTA 6 no Instagram Reels e TikTok.
Você decide sozinho o que é publicado: ninguém revisa depois de você. Na dúvida sobre segurança, rejeite.

Avalie o clipe e responda APENAS com o JSON pedido.

score (0 a 10): o quanto o clipe prende um espectador brasileiro rolando o feed.
- Pergunta central: ele prende nos primeiros 2 segundos?
- Peso maior para: gancho imediato, reação forte do streamer, caos/física absurda/momento engraçado de gameplay, desfecho claro.
- Tela parada, menu, loading, mapa ou conversa sem acontecimento: score no máximo 3.
- Se a fala principal NÃO for português do Brasil: score = 0.

safe = false se houver QUALQUER um destes:
- nudez ou conteúdo sexual;
- discurso de ódio, racismo, homofobia ou ofensa direcionada a pessoa real;
- dados pessoais (telefone, endereço, documento) ou exposição de terceiros fora do jogo;
- menor de idade aparente na câmera;
- violência real (fora do jogo);
- streamer em situação constrangedora fora do jogo;
- SPOILER de história: cutscene, missão da campanha, final, revelação de personagem.
Violência e crimes DENTRO do jogo são normais em GTA e não tornam o clipe inseguro.

title: até 60 caracteres, chamativo, sem mentir sobre o conteúdo, em PT-BR.
caption: 1 a 2 frases em PT-BR, tom de perfil de clipes, pode usar 1 ou 2 emojis.
hashtags: 3 a 6, incluindo #GTA6.
reason: 1 frase explicando a nota."""


class VerdictError(Exception):
    pass


class AIUnavailable(Exception):
    pass


def parse_verdict(text: str) -> Verdict:
    try:
        v = Verdict.model_validate_json(text)
    except ValidationError as e:
        raise VerdictError(str(e)) from e
    v.score = max(0.0, min(10.0, v.score))
    tags = [h.strip() for h in v.hashtags if h.strip()]
    v.hashtags = [h if h.startswith("#") else f"#{h}" for h in tags][:8]
    return v


def build_prompt(meta: dict) -> str:
    transcript = (meta.get("transcript") or "").strip() or "(sem fala detectada)"
    return (
        f"Plataforma: {meta.get('platform')} | Canal: {meta.get('channel')} | "
        f"Espectadores: {meta.get('viewers')} | Origem: {meta.get('source')}\n"
        f"Transcrição da fala:\n{transcript[:4000]}"
    )


PRICES_PER_MTOK = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
_SERVER_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2"}


def claude_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    inp, out = PRICES_PER_MTOK.get(model, (4.0, 20.0))
    return (input_tokens * inp + output_tokens * out) / 1_000_000


GeminiFn = Callable[[Path, str], Awaitable[str]]
ClaudeFn = Callable[[Path, str], Awaitable[tuple[str, float]]]


class Evaluator:
    def __init__(self, cfg: Settings, db: DB, gemini_fn: GeminiFn | None = None,
                 claude_fn: ClaudeFn | None = None, now_fn=utcnow):
        self.cfg = cfg
        self.db = db
        self.gemini_fn = gemini_fn or self._gemini
        self.claude_fn = claude_fn or self._claude
        self.now_fn = now_fn

    def _spend_key(self) -> str:
        return f"claude_spend:{local_day(self.now_fn()).isoformat()}"

    def claude_spent_today(self) -> float:
        return float(self.db.kv_get(self._spend_key(), "0") or 0)

    async def evaluate(self, video_path: Path, raw_path: Path, meta: dict) -> Verdict:
        prompt = build_prompt(meta)
        if self.cfg.gemini_api_key:
            try:
                return parse_verdict(await self.gemini_fn(video_path, prompt))
            except Exception as e:
                log.warning("Gemini falhou: %s", e)
        if self.cfg.anthropic_api_key:
            if self.claude_spent_today() >= self.cfg.claude_daily_budget_usd:
                log.warning("teto diário do Claude atingido")
            else:
                try:
                    text, cost = await self.claude_fn(raw_path, prompt)
                    self.db.kv_incr_float(self._spend_key(), cost)
                    return parse_verdict(text)
                except Exception as e:
                    log.warning("Claude falhou: %s", e)
        raise AIUnavailable("nenhuma IA disponível")

    async def _gemini(self, video_path: Path, prompt: str) -> str:
        def call() -> str:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.cfg.gemini_api_key)
            data = Path(video_path).read_bytes()
            if len(data) < 18 * 1024 * 1024:
                video = types.Part.from_bytes(data=data, mime_type="video/mp4")
            else:
                video = client.files.upload(file=str(video_path))
                while video.state and video.state.name == "PROCESSING":
                    time.sleep(2)
                    video = client.files.get(name=video.name)
            resp = client.models.generate_content(
                model=self.cfg.gemini_model,
                contents=[video, SYSTEM_PROMPT + "\n\n" + prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=Verdict,
                    temperature=0.2,
                ),
            )
            return resp.text

        return await asyncio.to_thread(call)

    async def _claude(self, raw_path: Path, prompt: str) -> tuple[str, float]:
        import anthropic

        frames = await extract_frames(Path(raw_path), Path(raw_path).parent / "frames", 8)
        content: list[dict] = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.standard_b64encode(p.read_bytes()).decode()}}
            for p in frames
        ]
        content.append({"type": "text", "text": prompt + "\n\nVocê recebeu "
                        f"{len(frames)} frames em ordem cronológica do clipe (não o vídeo)."})
        model = self.cfg.claude_model
        extra = {}
        if model in _SERVER_FALLBACK_MODELS:
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        client = anthropic.AsyncAnthropic(api_key=self.cfg.anthropic_api_key)
        resp = await client.beta.messages.create(
            model=model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
            messages=[{"role": "user", "content": content}],
            **extra,
        )
        cost = claude_cost_usd(model, resp.usage.input_tokens, resp.usage.output_tokens)
        if resp.stop_reason == "refusal":
            refused = Verdict(score=0, safe=False, reason="recusado pelo modelo",
                              title="", caption="", hashtags=[])
            return refused.model_dump_json(), cost
        text = "".join(b.text for b in resp.content if b.type == "text")
        return text, cost
