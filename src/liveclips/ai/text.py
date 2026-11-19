"""IA só de texto (sem vídeo), com resposta em JSON: Gemini primeiro, Claude de reserva."""
from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel

from ..core.timeutil import local_day, utcnow
from .claude import _client as _claude_client
from .claude import claude_cost_usd
from .evaluator import AIUnavailable
from .gemini import _client as _gemini_client
from .gemini import model_list

log = logging.getLogger(__name__)


async def _gemini_text(cfg, prompt: str, model_cls: type[BaseModel]) -> str:
    def call() -> str:
        from google.genai import types

        config = types.GenerateContentConfig(response_mime_type="application/json",
                                             response_schema=model_cls, temperature=0.3)
        keys = cfg.gemini_api_keys or ((cfg.gemini_api_key,) if cfg.gemini_api_key else ())
        errors = []
        for model in model_list(cfg.gemini_model):
            for n, key in enumerate(keys, 1):
                try:
                    resp = _gemini_client(key).models.generate_content(
                        model=model, contents=[prompt], config=config)
                    return resp.text
                except Exception as e:
                    errors.append(f"{model}/chave{n}: {str(e)[:100]}")
        raise RuntimeError("Gemini (texto) falhou: " + " | ".join(errors))

    return await asyncio.to_thread(call)


async def _claude_text(cfg, prompt: str, model_cls: type[BaseModel], db=None) -> str:
    key = f"claude_spend:{local_day(utcnow()).isoformat()}"
    if db is not None and float(db.kv_get(key, "0") or 0) >= cfg.claude_daily_budget_usd:
        raise RuntimeError("teto diário do Claude atingido")
    # mesma forma do claude_evaluate (beta.messages.create aceita output_config)
    resp = await _claude_client(cfg.anthropic_api_key).beta.messages.create(
        model=cfg.claude_model, max_tokens=4000,
        output_config={"effort": "low", "format": {"type": "json_schema",
                                                   "schema": model_cls.model_json_schema()}},
        messages=[{"role": "user", "content": prompt}])
    if db is not None:
        db.kv_incr_float(key, claude_cost_usd(cfg.claude_model, resp.usage.input_tokens,
                                              resp.usage.output_tokens))
    return "".join(b.text for b in resp.content if b.type == "text")


async def ask_json(cfg, prompt: str, model_cls: type[BaseModel], db=None,
                   gemini=_gemini_text, claude=_claude_text) -> BaseModel:
    errors = []
    if cfg.gemini_api_keys or cfg.gemini_api_key:
        try:
            return model_cls.model_validate_json(await gemini(cfg, prompt, model_cls))
        except Exception as e:
            log.warning("IA de texto (Gemini) falhou: %s", str(e)[:200])
            errors.append(str(e)[:200])
    if cfg.anthropic_api_key:
        try:
            return model_cls.model_validate_json(await claude(cfg, prompt, model_cls, db=db))
        except Exception as e:
            log.warning("IA de texto (Claude) falhou: %s", str(e)[:200])
            errors.append(str(e)[:200])
    raise AIUnavailable("; ".join(errors) or "nenhuma IA configurada")
