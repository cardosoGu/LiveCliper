"""Servidor HTTP: entrega o MP4 para o Instagram baixar e responde /health."""
from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Callable

import httpx
from aiohttp import web

from .config import Settings
from .publishers.base import PublishError


class MediaServer:
    def __init__(self, cfg: Settings, http: httpx.AsyncClient | None,
                 clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self.http = http
        self.clock = clock
        self.tokens: dict[str, tuple[Path, float]] = {}
        self.app = web.Application()
        self.app.router.add_get("/health", self._health)
        self.app.router.add_get("/media/{token}.mp4", self._media)

    async def public_base(self) -> str:
        if self.cfg.public_base_url:
            return self.cfg.public_base_url
        if self.cfg.railway_public_domain:
            return f"https://{self.cfg.railway_public_domain}"
        if self.http is not None and self.cfg.tunnel_metrics_url:
            try:
                r = await self.http.get(self.cfg.tunnel_metrics_url, timeout=5)
                host = r.json().get("hostname")
                if host:
                    return f"https://{host}"
            except Exception as e:
                raise PublishError(f"túnel indisponível: {e}") from e
        raise PublishError("nenhuma URL pública configurada")

    async def expose(self, path: Path | str, ttl_s: float = 3600) -> str:
        base = await self.public_base()
        token = secrets.token_urlsafe(16)
        self.tokens[token] = (Path(path), self.clock() + ttl_s)
        now = self.clock()
        self.tokens = {k: v for k, v in self.tokens.items() if v[1] > now}
        return f"{base}/media/{token}.mp4"

    async def _health(self, request: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def _media(self, request: web.Request) -> web.StreamResponse:
        entry = self.tokens.get(request.match_info["token"])
        if not entry or entry[1] < self.clock() or not entry[0].exists():
            raise web.HTTPNotFound()
        return web.FileResponse(entry[0], headers={"Content-Type": "video/mp4"})

    async def start(self) -> None:
        runner = web.AppRunner(self.app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.cfg.port).start()
