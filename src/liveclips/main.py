"""Ponto de entrada: liga todos os módulos e os mantém rodando."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from .ai.evaluator import Evaluator
from .capture.kick.public_clips import collect_kick
from .capture.loop import collector_loop
from .capture.spike import SpikeDetector
from .capture.twitch.api import TwitchAPI
from .capture.twitch.chat import ChatMonitor
from .capture.twitch.clipper import Clipper, TokenBucket
from .capture.twitch.discovery import discovery_loop
from .capture.twitch.public_clips import collect_twitch_public
from .control.monitor import LogTransport, Monitor
from .core.config import Settings, load_settings
from .core.db import DB
from .core.supervisor import supervise
from .processing.cleanup import cleanup_loop
from .processing.pipeline import Pipeline
from .publishing.media_server import MediaServer
from .publishing.platforms import build_publishers
from .publishing.scheduler import Scheduler

log = logging.getLogger("liveclips")


def check_settings(cfg: Settings) -> list[str]:
    missing = [name for name, value in [
        ("TWITCH_CLIENT_ID", cfg.twitch_client_id),
        ("TWITCH_CLIENT_SECRET", cfg.twitch_client_secret),
    ] if not value]
    if not (cfg.gemini_api_key or cfg.anthropic_api_key):
        missing.append("GEMINI_API_KEY ou ANTHROPIC_API_KEY")
    return missing


async def amain() -> None:
    cfg = load_settings()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    missing = check_settings(cfg)
    if missing:
        raise SystemExit("Variáveis obrigatórias faltando: " + ", ".join(missing))

    data = Path(cfg.data_dir)
    media_dir = data / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    db = DB(str(data / "liveclips.db"))
    db.reset_processing()

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
        bot = None
        if cfg.discord_bot_token and cfg.discord_channel_id:
            from .control.discord_bot import DiscordBot

            bot = DiscordBot(cfg.discord_bot_token, cfg.discord_channel_id,
                             cfg.discord_admin_user_id)
            transport = bot
        else:
            log.warning("Discord não configurado: avisos vão só para o log")
            transport = LogTransport()

        twitch = TwitchAPI(
            cfg.twitch_client_id, cfg.twitch_client_secret,
            db.kv_get("twitch_refresh_token") or cfg.twitch_bot_refresh_token, http,
            on_refresh=lambda t: db.kv_set("twitch_refresh_token", t))
        game_id = await twitch.get_game_id(cfg.twitch_game_name)
        log.info("categoria '%s' = %s", cfg.twitch_game_name, game_id)

        viewers_cache: dict[str, int] = {}
        clipper = Clipper(twitch, db, TokenBucket(cfg.clip_rate_seconds))
        chat = ChatMonitor(
            on_spike=clipper.on_spike,
            detector_factory=lambda: SpikeDetector(multiplier=cfg.spike_multiplier,
                                                   min_msgs=cfg.spike_min_msgs))
        publishers = build_publishers(cfg, db, http)
        media_server = MediaServer(cfg, http)
        monitor = Monitor(db, transport, {p.name: p for p in publishers}, cfg,
                          channel_count=lambda: len(chat.channels))
        if bot is not None:
            bot.attach(monitor)
        pipeline = Pipeline(db, cfg, Evaluator(cfg, db), http, media_dir)
        scheduler = Scheduler(db, cfg, publishers, media_server, monitor)

        await media_server.start()
        jobs = {
            "chat": chat.run,
            "discovery": lambda: discovery_loop(twitch, game_id, chat, db,
                                                cfg.monitor_max_channels, viewers_cache),
            "clipper": clipper.run,
            "twitch_clips": lambda: collector_loop(
                lambda now: collect_twitch_public(twitch, game_id, db, viewers_cache, now),
                180, "twitch_clips"),
            "pipeline": pipeline.run,
            "scheduler": scheduler.run,
            "cleanup": lambda: cleanup_loop(media_dir),
        }
        if bot is not None:
            jobs["discord"] = bot.run
        if cfg.kick_enabled:
            jobs["kick_clips"] = lambda: collector_loop(
                lambda now: collect_kick(http, cfg.kick_category_slug, db, now),
                300, "kick_clips")

        plats = ", ".join(p.name for p in publishers) or "nenhuma"
        asyncio.get_running_loop().call_later(
            3, lambda: asyncio.create_task(monitor.info(
                f"🤖 **liveclips iniciado** · categoria: {cfg.twitch_game_name} · "
                f"plataformas: {plats}" + (" · 🧪 MODO ENSAIO" if cfg.dry_run else ""))))
        await asyncio.gather(*(supervise(n, f, monitor.alert) for n, f in jobs.items()))


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
