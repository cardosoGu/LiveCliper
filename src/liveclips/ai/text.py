"""IA só de texto (sem vídeo), com resposta em JSON, pelo Gemini."""
from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel

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


async def ask_json(cfg, prompt: str, model_cls: type[BaseModel], db=None,
                   gemini=_gemini_text) -> BaseModel:
    if not (cfg.gemini_api_keys or cfg.gemini_api_key):
        raise AIUnavailable("nenhuma chave do Gemini configurada")
    try:
        return model_cls.model_validate_json(await gemini(cfg, prompt, model_cls))
    except Exception as e:
        log.warning("IA de texto (Gemini) falhou: %s", str(e)[:200])
        raise AIUnavailable(str(e)[:200]) from e
