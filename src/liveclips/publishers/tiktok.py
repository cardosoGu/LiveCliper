"""TikTok Content Posting API (Direct Post, upload de arquivo em 1 chunk).

Sem auditoria do app, só `SELF_ONLY` (privado) funciona.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import httpx

from .base import PostResult, PublishError, PublishJob

TT = "https://open.tiktokapis.com/v2"


class TikTokPublisher:
    name = "tiktok"

    def __init__(self, client_key: str, client_secret: str, refresh_token: str,
                 privacy: str, http: httpx.AsyncClient,
                 on_refresh: Callable[[str], None] | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.client_key = client_key
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.privacy = privacy
        self.http = http
        self.on_refresh = on_refresh
        self.sleep = sleep

    async def _token(self) -> str:
        r = await self.http.post(f"{TT}/oauth/token/", data={
            "client_key": self.client_key, "client_secret": self.client_secret,
            "grant_type": "refresh_token", "refresh_token": self.refresh_token,
        })
        j = r.json()
        if "access_token" not in j:
            raise PublishError(f"tiktok token: {j}")
        new = j.get("refresh_token")
        if new and new != self.refresh_token:
            self.refresh_token = new
            if self.on_refresh:
                self.on_refresh(new)
        return j["access_token"]

    async def publish(self, job: PublishJob) -> PostResult:
        token = await self._token()
        headers = {"Authorization": f"Bearer {token}",
                   "Content-Type": "application/json; charset=UTF-8"}
        data = job.video_path.read_bytes()
        size = len(data)
        init = await self.http.post(f"{TT}/post/publish/video/init/", headers=headers, json={
            "post_info": {"title": job.caption[:2200], "privacy_level": self.privacy,
                          "disable_duet": False, "disable_comment": False,
                          "disable_stitch": False},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                            "chunk_size": size, "total_chunk_count": 1},
        })
        j = init.json()
        if j.get("error", {}).get("code", "ok") != "ok":
            raise PublishError(f"tiktok init: {j.get('error')}")
        upload_url = j["data"]["upload_url"]
        publish_id = j["data"]["publish_id"]
        put = await self.http.put(upload_url, content=data, headers={
            "Content-Type": "video/mp4", "Content-Range": f"bytes 0-{size - 1}/{size}"})
        if put.status_code not in (200, 201):
            raise PublishError(f"tiktok upload: {put.status_code} {put.text[:200]}")
        for _ in range(60):
            st = await self.http.post(f"{TT}/post/publish/status/fetch/", headers=headers,
                                      json={"publish_id": publish_id})
            status = st.json().get("data", {}).get("status")
            if status == "PUBLISH_COMPLETE":
                return PostResult(self.name, publish_id, "")
            if status == "FAILED":
                raise PublishError(f"tiktok falhou: {st.json()}")
            await self.sleep(5)
        raise PublishError("tiktok: status não concluiu em 5 min")
