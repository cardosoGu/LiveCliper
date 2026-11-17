"""Instagram Reels via Graph API (conta Business/Creator ligada a página do Facebook)."""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import httpx

from .base import PostResult, PublishError, PublishJob


class InstagramPublisher:
    name = "instagram"

    def __init__(self, ig_user_id: str, access_token: str, http: httpx.AsyncClient,
                 version: str = "v23.0",
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.uid = ig_user_id
        self.token = access_token
        self.http = http
        self.base = f"https://graph.facebook.com/{version}"
        self.sleep = sleep

    async def publish(self, job: PublishJob) -> PostResult:
        r = await self.http.post(f"{self.base}/{self.uid}/media", data={
            "media_type": "REELS", "video_url": job.video_url, "caption": job.caption,
            "share_to_feed": "true", "access_token": self.token,
        })
        if r.status_code >= 400:
            raise PublishError(f"instagram container: {r.text[:300]}")
        container = r.json()["id"]
        for _ in range(60):
            s = await self.http.get(f"{self.base}/{container}", params={
                "fields": "status_code,status", "access_token": self.token})
            code = s.json().get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise PublishError(f"instagram container {code}: {s.json().get('status')}")
            await self.sleep(5)
        else:
            raise PublishError("instagram: container não terminou em 5 min")
        p = await self.http.post(f"{self.base}/{self.uid}/media_publish", data={
            "creation_id": container, "access_token": self.token})
        if p.status_code >= 400:
            raise PublishError(f"instagram publish: {p.text[:300]}")
        media_id = p.json()["id"]
        link = await self.http.get(f"{self.base}/{media_id}", params={
            "fields": "permalink", "access_token": self.token})
        url = link.json().get("permalink", "") if link.status_code == 200 else ""
        return PostResult(self.name, media_id, url)

    async def delete(self, post_id: str) -> bool:
        r = await self.http.delete(f"{self.base}/{post_id}",
                                   params={"access_token": self.token})
        return r.status_code == 200
