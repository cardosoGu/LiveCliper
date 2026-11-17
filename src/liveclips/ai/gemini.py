"""Gemini: avalia o vídeo inteiro (inline até 18 MB, Files API acima disso)."""
from __future__ import annotations

import asyncio
import logging
import time
from functools import lru_cache
from pathlib import Path

from ..core.config import Settings
from .prompt import SYSTEM_PROMPT
from .verdict import Verdict

INLINE_LIMIT = 18 * 1024 * 1024
log = logging.getLogger(__name__)


@lru_cache(maxsize=2)
def _client(api_key: str):
    """Um cliente por chave, reaproveitando conexões entre avaliações."""
    from google import genai

    return genai.Client(api_key=api_key)


def model_list(setting: str) -> list[str]:
    """GEMINI_MODEL aceita vários modelos separados por vírgula, em ordem de preferência."""
    return [m.strip() for m in setting.split(",") if m.strip()]


async def gemini_evaluate(cfg: Settings, video_path: Path, prompt: str) -> str:
    """Tenta cada modelo da lista; um modelo sobrecarregado (503) ou indisponível
    não derruba a avaliação enquanto houver outro."""
    def call() -> str:
        from google.genai import types

        data = Path(video_path).read_bytes()
        config = types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=Verdict, temperature=0.2)
        keys = cfg.gemini_api_keys or ((cfg.gemini_api_key,) if cfg.gemini_api_key else ())
        errors = []
        # Para cada modelo, tenta todas as chaves (1, 2, 3...) antes de ir ao próximo modelo.
        for model in model_list(cfg.gemini_model):
            for n, key in enumerate(keys, 1):
                client = _client(key)
                try:
                    if len(data) < INLINE_LIMIT:
                        video = types.Part.from_bytes(data=data, mime_type="video/mp4")
                    else:
                        video = client.files.upload(file=str(video_path))
                        while video.state and video.state.name == "PROCESSING":
                            time.sleep(2)
                            video = client.files.get(name=video.name)
                    resp = client.models.generate_content(
                        model=model, contents=[video, SYSTEM_PROMPT + "\n\n" + prompt],
                        config=config)
                    return resp.text
                except Exception as e:
                    log.warning("Gemini %s (chave %d) falhou: %s", model, n, str(e)[:200])
                    errors.append(f"{model}/chave{n}: {str(e)[:100]}")
        raise RuntimeError("todos os modelos Gemini falharam: " + " | ".join(errors))

    return await asyncio.to_thread(call)
