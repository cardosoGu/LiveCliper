"""Claude (reserva paga): avalia 8 frames do clipe + transcrição."""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

from ..core.config import Settings
from ..processing.ffmpeg import extract_frames
from .prompt import SYSTEM_PROMPT
from .verdict import VERDICT_SCHEMA, refusal_verdict

PRICES_PER_MTOK = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
# Modelos que aceitam o fallback de recusa feito pelo servidor (Haiku não aceita).
SERVER_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2"}


@lru_cache(maxsize=2)
def _client(api_key: str):
    """Um cliente por chave, reaproveitando conexões entre avaliações."""
    import anthropic

    return anthropic.AsyncAnthropic(api_key=api_key)


def claude_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    inp, out = PRICES_PER_MTOK.get(model, (4.0, 20.0))
    return (input_tokens * inp + output_tokens * out) / 1_000_000


async def claude_evaluate(cfg: Settings, raw_path: Path, prompt: str) -> tuple[str, float]:
    """Retorna (json do veredito, custo em US$)."""
    raw = Path(raw_path)
    frames = await extract_frames(raw, raw.parent / f"frames_{raw.stem}", 8)
    content: list[dict] = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": base64.standard_b64encode(p.read_bytes()).decode()}}
        for p in frames
    ]
    content.append({"type": "text", "text": prompt + "\n\nVocê recebeu "
                    f"{len(frames)} frames em ordem cronológica do clipe (não o vídeo)."})
    model = cfg.claude_model
    extra = {}
    if model in SERVER_FALLBACK_MODELS:
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    resp = await _client(cfg.anthropic_api_key).beta.messages.create(
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
        return refusal_verdict().model_dump_json(), cost
    return "".join(b.text for b in resp.content if b.type == "text"), cost
