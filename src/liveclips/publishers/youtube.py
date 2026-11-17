"""YouTube Shorts via Data API v3. Projetos não auditados sobem como privado."""
from __future__ import annotations

import asyncio

from .base import PostResult, PublishJob

SCOPES = ["https://www.googleapis.com/auth/youtube"]


class YouTubePublisher:
    name = "youtube"

    def __init__(self, client_id: str, client_secret: str, refresh_token: str, privacy: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.privacy = privacy

    def _service(self):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(None, refresh_token=self.refresh_token,
                            token_uri="https://oauth2.googleapis.com/token",
                            client_id=self.client_id, client_secret=self.client_secret,
                            scopes=SCOPES)
        return build("youtube", "v3", credentials=creds, cache_discovery=False)

    async def publish(self, job: PublishJob) -> PostResult:
        def call() -> str:
            from googleapiclient.http import MediaFileUpload

            body = {
                "snippet": {"title": (job.title or "GTA 6")[:100],
                            "description": job.caption[:5000], "categoryId": "20"},
                "status": {"privacyStatus": self.privacy, "selfDeclaredMadeForKids": False},
            }
            req = self._service().videos().insert(
                part="snippet,status", body=body,
                media_body=MediaFileUpload(str(job.video_path), mimetype="video/mp4",
                                           resumable=True))
            resp = None
            while resp is None:
                _, resp = req.next_chunk()
            return resp["id"]

        vid = await asyncio.to_thread(call)
        return PostResult(self.name, vid, f"https://youtube.com/shorts/{vid}")

    async def delete(self, post_id: str) -> bool:
        def call() -> None:
            self._service().videos().delete(id=post_id).execute()

        try:
            await asyncio.to_thread(call)
            return True
        except Exception:
            return False
