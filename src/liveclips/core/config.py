"""Configuração lida 100% de variáveis de ambiente."""
from __future__ import annotations

import os
import re
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
    # twitch (opcional: só para live em andamento)
    twitch_client_id: str
    twitch_client_secret: str
    # IA
    gemini_api_key: str  # a primeira chave (compatibilidade)
    gemini_api_keys: tuple[str, ...]
    gemini_model: str
    groq_api_key: str
    # discord
    discord_bot_token: str
    discord_channel_id: int
    discord_admin_user_id: int
    discord_upload_limit_mb: float
    discord_approval_channel_id: int
    discord_panel_channel_id: int
    discord_links_channel_id: int
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
    ai_daily_budget: int
    ai_min_score: float
    post_min_interval_min: int
    post_max_per_day: int
    ondemand_max_hours: float

    @property
    def discord_channel_ids(self) -> dict[str, int]:
        """Canais informados por ID no .env (os que faltarem o bot cria)."""
        ids = {"logs": self.discord_channel_id,
               "aprovação": self.discord_approval_channel_id,
               "painel": self.discord_panel_channel_id,
               "gerar-clipes": self.discord_links_channel_id}
        return {name: cid for name, cid in ids.items() if cid}


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

    def mode(key: str, allowed: tuple[str, ...] = ("off", "private", "public")) -> str:
        v = s(key, "off").lower()
        if v not in allowed:
            raise ValueError(f"{key} deve ser {', '.join(allowed)} (recebido: {v})")
        return v

    # GEMINI_API_KEY e/ou GEMINI_API_KEY1, GEMINI_API_KEY2, ... em ordem de uso
    numbered = sorted((int(m.group(1)), k) for k in e
                      if (m := re.fullmatch(r"GEMINI_API_KEY(\d+)", k)))
    gemini_keys = tuple(dict.fromkeys(
        v for v in [s("GEMINI_API_KEY")] + [s(k) for _, k in numbered] if v))

    return Settings(
        data_dir=s("DATA_DIR", "/data"),
        port=i("PORT", 8080),
        public_base_url=s("PUBLIC_BASE_URL").rstrip("/"),
        railway_public_domain=s("RAILWAY_PUBLIC_DOMAIN"),
        tunnel_metrics_url=s("TUNNEL_METRICS_URL", "http://cloudflared:2000/quicktunnel"),
        dry_run=b("DRY_RUN", False),
        twitch_client_id=s("TWITCH_CLIENT_ID"),
        twitch_client_secret=s("TWITCH_CLIENT_SECRET"),
        gemini_api_key=(gemini_keys[0] if gemini_keys else ""),
        gemini_api_keys=gemini_keys,
        gemini_model=s("GEMINI_MODEL", "gemini-3.5-flash,gemini-3.8-flash"),
        groq_api_key=s("GROQ_API_KEY"),
        discord_bot_token=s("DISCORD_BOT_TOKEN"),
        discord_channel_id=i("DISCORD_CHANNEL_ID", 0),
        discord_admin_user_id=i("DISCORD_ADMIN_USER_ID", 0),
        discord_upload_limit_mb=f("DISCORD_UPLOAD_LIMIT_MB", 10.0),
        discord_approval_channel_id=i("DISCORD_APPROVAL_CHANNEL_ID", 0),
        discord_panel_channel_id=i("DISCORD_PANEL_CHANNEL_ID", 0),
        discord_links_channel_id=i("DISCORD_LINKS_CHANNEL_ID", 0),
        ig_user_id=s("IG_USER_ID"),
        ig_access_token=s("IG_ACCESS_TOKEN"),
        ig_graph_version=s("IG_GRAPH_VERSION", "v23.0"),
        tiktok_mode=mode("TIKTOK_MODE", ("off", "draft", "private", "public")),
        tiktok_client_key=s("TIKTOK_CLIENT_KEY"),
        tiktok_client_secret=s("TIKTOK_CLIENT_SECRET"),
        tiktok_refresh_token=s("TIKTOK_REFRESH_TOKEN"),
        yt_mode=mode("YT_MODE"),
        yt_client_id=s("YT_CLIENT_ID"),
        yt_client_secret=s("YT_CLIENT_SECRET"),
        yt_refresh_token=s("YT_REFRESH_TOKEN"),
        ai_daily_budget=i("AI_DAILY_BUDGET", 300),
        ai_min_score=f("AI_MIN_SCORE", 6.0),
        post_min_interval_min=i("POST_MIN_INTERVAL_MIN", 45),
        post_max_per_day=i("POST_MAX_PER_DAY", 8),
        ondemand_max_hours=f("ONDEMAND_MAX_HOURS", 4.0),
    )
