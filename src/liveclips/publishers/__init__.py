from __future__ import annotations

import httpx

from ..config import Settings
from ..db import DB
from .base import PostResult, PublishError, PublishJob
from .caption import build_caption
from .instagram import InstagramPublisher
from .tiktok import TikTokPublisher
from .youtube import YouTubePublisher

__all__ = ["PostResult", "PublishError", "PublishJob", "build_caption", "build_publishers",
           "InstagramPublisher", "TikTokPublisher", "YouTubePublisher"]


def build_publishers(cfg: Settings, db: DB, http: httpx.AsyncClient) -> list:
    pubs: list = []
    if cfg.ig_user_id and cfg.ig_access_token:
        pubs.append(InstagramPublisher(cfg.ig_user_id, cfg.ig_access_token, http,
                                       cfg.ig_graph_version))
    if cfg.tiktok_mode != "off" and cfg.tiktok_client_key:
        pubs.append(TikTokPublisher(
            cfg.tiktok_client_key, cfg.tiktok_client_secret,
            db.kv_get("tiktok_refresh_token") or cfg.tiktok_refresh_token,
            "SELF_ONLY" if cfg.tiktok_mode == "private" else "PUBLIC_TO_EVERYONE",
            http, on_refresh=lambda t: db.kv_set("tiktok_refresh_token", t)))
    if cfg.yt_mode != "off" and cfg.yt_client_id:
        pubs.append(YouTubePublisher(cfg.yt_client_id, cfg.yt_client_secret,
                                     cfg.yt_refresh_token, cfg.yt_mode))
    return pubs
