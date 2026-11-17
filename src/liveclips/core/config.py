"""Configuração lida 100% de variáveis de ambiente."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    # infraestrutura
    data_dir: str
    port: int
    public_base_url: str
    railway_public_domain: str
    tunnel_metrics_url: str
    dry_run: bool
    # twitch / kick
    twitch_client_id: str
    twitch_client_secret: str
    twitch_bot_refresh_token: str
    twitch_game_name: str
    kick_enabled: bool
    kick_category_slug: str
    # IA
    gemini_api_key: str
    gemini_model: str
    anthropic_api_key: str
    claude_model: str
    claude_daily_budget_usd: float
    groq_api_key: str
    # discord
    discord_bot_token: str
    discord_channel_id: int
    discord_admin_user_id: int
    # plataformas
    ig_user_id: str
    ig_access_token: str
    ig_graph_version: str
    tiktok_mode: str
    tiktok_client_key: str
    tiktok_client_secret: str
    tiktok_refresh_token: str
    yt_mode: str
    yt_client_id: str
    yt_client_secret: str
    yt_refresh_token: str
    # ajustes
    monitor_max_channels: int
    spike_multiplier: float
    spike_min_msgs: int
    clip_rate_seconds: float
    rank_threshold: float
    ai_daily_budget: int
    ai_min_score: float
    post_min_interval_min: int
    post_max_per_day: int
    post_max_per_day_step: int
    post_max_per_day_cap: int
    queue_ttl_min: int


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    e = dict(os.environ if env is None else env)

    def s(key: str, default: str = "") -> str:
        v = e.get(key)
        return default if v is None or v.strip() == "" else v.strip()

    def i(key: str, default: int) -> int:
        return int(s(key, str(default)))

    def f(key: str, default: float) -> float:
        return float(s(key, str(default)))

    def b(key: str, default: bool) -> bool:
        return s(key, "1" if default else "0").lower() in {"1", "true", "yes", "on"}

    def mode(key: str) -> str:
        v = s(key, "off").lower()
        if v not in {"off", "private", "public"}:
            raise ValueError(f"{key} deve ser off, private ou public (recebido: {v})")
        return v

    return Settings(
        data_dir=s("DATA_DIR", "/data"),
        port=i("PORT", 8080),
        public_base_url=s("PUBLIC_BASE_URL").rstrip("/"),
        railway_public_domain=s("RAILWAY_PUBLIC_DOMAIN"),
        tunnel_metrics_url=s("TUNNEL_METRICS_URL", "http://cloudflared:2000/quicktunnel"),
        dry_run=b("DRY_RUN", False),
        twitch_client_id=s("TWITCH_CLIENT_ID"),
        twitch_client_secret=s("TWITCH_CLIENT_SECRET"),
        twitch_bot_refresh_token=s("TWITCH_BOT_REFRESH_TOKEN"),
        twitch_game_name=s("TWITCH_GAME_NAME", "Grand Theft Auto VI"),
        kick_enabled=b("KICK_ENABLED", False),
        kick_category_slug=s("KICK_CATEGORY_SLUG", "grand-theft-auto-vi"),
        gemini_api_key=s("GEMINI_API_KEY"),
        gemini_model=s("GEMINI_MODEL", "gemini-3.5-flash,gemini-3.8-flash"),
        anthropic_api_key=s("ANTHROPIC_API_KEY"),
        claude_model=s("CLAUDE_MODEL", "claude-opus-5-5"),
        claude_daily_budget_usd=f("CLAUDE_DAILY_BUDGET_USD", 3.0),
        groq_api_key=s("GROQ_API_KEY"),
        discord_bot_token=s("DISCORD_BOT_TOKEN"),
        discord_channel_id=i("DISCORD_CHANNEL_ID", 0),
        discord_admin_user_id=i("DISCORD_ADMIN_USER_ID", 0),
        ig_user_id=s("IG_USER_ID"),
        ig_access_token=s("IG_ACCESS_TOKEN"),
        ig_graph_version=s("IG_GRAPH_VERSION", "v23.0"),
        tiktok_mode=mode("TIKTOK_MODE"),
        tiktok_client_key=s("TIKTOK_CLIENT_KEY"),
        tiktok_client_secret=s("TIKTOK_CLIENT_SECRET"),
        tiktok_refresh_token=s("TIKTOK_REFRESH_TOKEN"),
        yt_mode=mode("YT_MODE"),
        yt_client_id=s("YT_CLIENT_ID"),
        yt_client_secret=s("YT_CLIENT_SECRET"),
        yt_refresh_token=s("YT_REFRESH_TOKEN"),
        monitor_max_channels=i("MONITOR_MAX_CHANNELS", 200),
        spike_multiplier=f("SPIKE_MULTIPLIER", 3.0),
        spike_min_msgs=i("SPIKE_MIN_MSGS", 15),
        clip_rate_seconds=f("CLIP_RATE_SECONDS", 20.0),
        rank_threshold=f("RANK_THRESHOLD", 5.0),
        ai_daily_budget=i("AI_DAILY_BUDGET", 300),
        ai_min_score=f("AI_MIN_SCORE", 7.0),
        post_min_interval_min=i("POST_MIN_INTERVAL_MIN", 45),
        post_max_per_day=i("POST_MAX_PER_DAY", 8),
        post_max_per_day_step=i("POST_MAX_PER_DAY_STEP", 2),
        post_max_per_day_cap=i("POST_MAX_PER_DAY_CAP", 20),
        queue_ttl_min=i("QUEUE_TTL_MIN", 90),
    )
