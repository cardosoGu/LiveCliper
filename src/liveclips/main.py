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
from .control.approval import ApprovalDesk, LogApprovalTransport
from .control.manual_test import ManualTester
from .control.monitor import LogTransport, Monitor
from .control.runtime import RuntimeSettings
from .core.config import Settings, load_settings
from .core.db import DB
from .core.supervisor import supervise
from .processing.cleanup import cleanup_loop
from .processing.ffmpeg import make_preview
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
    if not (cfg.gemini_api_keys or cfg.anthropic_api_key):
        missing.append("GEMINI_API_KEY1 (ou GEMINI_API_KEY) ou ANTHROPIC_API_KEY")
    return missing


async def amain() -> None:
    cfg = load_settings()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx registra a URL completa de cada requisição em INFO; algumas APIs
    # (Instagram) recebem o token na URL, então esse log vazaria credenciais.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    missing = check_settings(cfg)
    if missing:
        raise SystemExit("Variáveis obrigatórias faltando: " + ", ".join(missing))

    data = Path(cfg.data_dir)
    media_dir = data / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    db = DB(str(data / "liveclips.db"))
    db.reset_processing()
    runtime = RuntimeSettings(db, cfg)

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
        bot = None
        if cfg.discord_bot_token and cfg.discord_channel_id:
            from .control.discord_bot import DiscordBot

            bot = DiscordBot(cfg.discord_bot_token, cfg.discord_channel_id,
                             cfg.discord_admin_user_id, db)
            transport = bot
        else:
            log.warning("Discord não configurado: avisos vão só para o log")
            transport = LogTransport()

        twitch = TwitchAPI(
            cfg.twitch_client_id, cfg.twitch_client_secret,
            db.kv_get("twitch_refresh_token") or cfg.twitch_bot_refresh_token, http,
            on_refresh=lambda t: db.kv_set("twitch_refresh_token", t))
        found = await twitch.get_game_ids(cfg.twitch_game_names)
        missing = [n for n in cfg.twitch_game_names if n.lower() not in
                   {f.lower() for f in found}]
        if missing:
            log.warning("categorias não encontradas na Twitch (ignoradas): %s", missing)
        if not found:
            raise SystemExit(f"nenhuma categoria encontrada: {cfg.twitch_game_name}")
        game_id = list(found.values())
        log.info("categorias: %s", found)

        viewers_cache: dict[str, int] = {}
        clipper = Clipper(twitch, db, TokenBucket(cfg.clip_rate_seconds),
                          followup_delay_s=cfg.clip_followup_s)
        chat = ChatMonitor(
            on_spike=clipper.on_spike,
            detector_factory=lambda: SpikeDetector(multiplier=cfg.spike_multiplier,
                                                   min_msgs=cfg.spike_min_msgs))
        publishers = build_publishers(cfg, db, http)
        media_server = MediaServer(cfg, http)
        monitor = Monitor(db, transport, {p.name: p for p in publishers}, cfg, runtime=runtime,
                          channel_count=lambda: len(chat.channels),
                          chat_summary=chat.summary)
        if bot is not None:
            bot.attach(monitor)
        pipeline = Pipeline(db, cfg, Evaluator(cfg, db), http, media_dir)
        monitor.tester = ManualTester(twitch, db, pipeline, chat,
                                      followup_delay_s=cfg.clip_followup_s)
        scheduler = Scheduler(db, cfg, publishers, media_server, monitor, runtime)
        if bot is not None:
            from .control.discord_bot import DiscordApprovalTransport

            approval_transport = DiscordApprovalTransport(bot)
        else:
            approval_transport = LogApprovalTransport()
        desk = ApprovalDesk(db, runtime, approval_transport, scheduler.ensure_rendered,
                            make_preview, media_server.expose,
                            [p.name for p in publishers], cfg.discord_upload_limit_mb)
        scheduler.on_status = desk.on_status
        if bot is not None:
            bot.attach_desk(desk)

        await media_server.start()
        jobs = {
            "chat": chat.run,
            "discovery": lambda: discovery_loop(twitch, game_id, chat, db,
                                                cfg.monitor_max_channels, viewers_cache,
                                                min_viewers=cfg.min_viewers),
            "clipper": clipper.run,
            "twitch_clips": lambda: collector_loop(
                lambda now: collect_twitch_public(twitch, list(chat.channels.values()),
                                                db, now),
                180, "twitch_clips"),
            "pipeline": pipeline.run,
            "scheduler": scheduler.run,
            "approval": desk.run,
            "notifier": monitor.run_sender,
            "cleanup": lambda: cleanup_loop(media_dir),
        }
        if bot is not None:
            jobs["discord"] = bot.run
        if cfg.kick_enabled:
            jobs["kick_clips"] = lambda: collector_loop(
                lambda now: collect_kick(http, cfg.kick_category_slug, db, now),
                300, "kick_clips")

        plats = ", ".join(p.name for p in publishers) or "nenhuma"
        # Vai para a fila de avisos; o sender entrega quando o Discord estiver pronto.
        await monitor.info(
            f"🤖 **liveclips iniciado** · categorias: {', '.join(found)} · "
            f"mín. {cfg.min_viewers} espectadores · "
            f"plataformas configuradas: {plats}")
        await asyncio.gather(*(supervise(n, f, monitor.alert) for n, f in jobs.items()))


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
