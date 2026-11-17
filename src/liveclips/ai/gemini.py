"""Gemini: avalia o vídeo inteiro (inline até 18 MB, Files API acima disso)."""
from __future__ import annotations

import asyncio
import time
from functools import lru_cache
from pathlib import Path

from ..core.config import Settings
from .prompt import SYSTEM_PROMPT
from .verdict import Verdict

INLINE_LIMIT = 18 * 1024 * 1024


@lru_cache(maxsize=2)
def _client(api_key: str):
    """Um cliente por chave, reaproveitando conexões entre avaliações."""
    from google import genai

    return genai.Client(api_key=api_key)


async def gemini_evaluate(cfg: Settings, video_path: Path, prompt: str) -> str:
    def call() -> str:
        from google.genai import types

        client = _client(cfg.gemini_api_key)
        data = Path(video_path).read_bytes()
        if len(data) < INLINE_LIMIT:
            video = types.Part.from_bytes(data=data, mime_type="video/mp4")
        else:
            video = client.files.upload(file=str(video_path))
            while video.state and video.state.name == "PROCESSING":
                time.sleep(2)
                video = client.files.get(name=video.name)
        resp = client.models.generate_content(
            model=cfg.gemini_model,
            contents=[video, SYSTEM_PROMPT + "\n\n" + prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=Verdict,
                temperature=0.2,
            ),
        )
        return resp.text

    return await asyncio.to_thread(call)
