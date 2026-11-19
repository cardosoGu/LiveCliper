"""Ponto de entrada: liga todos os módulos e os mantém rodando."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from .ai.evaluator import Evaluator
from .control.approval import ApprovalDesk, LogApprovalTransport
from .control.monitor import LogTransport, Monitor
from .control.panel import Panel
from .control.runtime import RuntimeSettings
from .core.config import Settings, load_settings
from .core.db import DB
from .core.timeutil import local_day, local_midnight_utc, utcnow
from .core.supervisor import supervise
from .processing.cleanup import cleanup_loop
from .processing.ffmpeg import make_preview
from .ondemand.jobs import LogJobNotifier, OnDemandRunner
from .ondemand.twitch import TwitchAPI
from .processing.pipeline import Pipeline
from .publishing.media_server import MediaServer
from .publishing.platforms import build_publishers
from .publishing.scheduler import Scheduler

log = logging.getLogger("liveclips")


def check_settings(cfg: Settings) -> list[str]:
    missing: list[str] = []
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
                             cfg.discord_admin_user_id, db, cfg.discord_channel_ids)
            transport = bot
        else:
            log.warning("Discord não configurado: avisos vão só para o log")
            transport = LogTransport()

        # Só com token de app; ainda sem uso aqui (o sob demanda passa a usar depois).
        twitch = (TwitchAPI(cfg.twitch_client_id, cfg.twitch_client_secret, http)
                  if cfg.twitch_client_id and cfg.twitch_client_secret else None)

        publishers = build_publishers(cfg, db, http)
        media_server = MediaServer(cfg, http)
        monitor = Monitor(db, transport, {p.name: p for p in publishers}, cfg, runtime=runtime)
        if bot is not None:
            bot.attach(monitor)

        pipeline = Pipeline(db, cfg, Evaluator(cfg, db), media_dir)
        if bot is not None:
            from .control.discord_bot import DiscordJobNotifier

            job_notifier = DiscordJobNotifier(bot)
        else:
            job_notifier = LogJobNotifier()
        from .ondemand import ingest as ondemand_ingest

        ondemand_ingest.COOKIES_FILE = data / "youtube-cookies.txt"  # opcional
        ondemand = OnDemandRunner(db, cfg, http, data / "jobs", media_dir, job_notifier,
                                 twitch=twitch)
        if bot is not None:
            bot.attach_ondemand(ondemand)
        scheduler = Scheduler(db, cfg, publishers, media_server, monitor, runtime)
        if bot is not None:
            from .control.discord_bot import DiscordApprovalTransport

            approval_transport = DiscordApprovalTransport(bot)
        else:
            approval_transport = LogApprovalTransport()
        desk = ApprovalDesk(db, runtime, approval_transport, scheduler.ensure_rendered,
                            make_preview, media_server.expose,
                            [p.name for p in publishers], cfg.discord_upload_limit_mb,
                            retry=scheduler.publish_to)
        scheduler.on_status = desk.on_status
        if bot is not None:
            bot.attach_desk(desk)

        def panel_status() -> list[str]:
            day = local_day(utcnow()).isoformat()
            evals = int(float(db.kv_get(f"ai_evals:{day}", "0") or 0))
            waiting = (len(db.awaiting_cards(posted=True, limit=1000))
                       + len(db.awaiting_cards(posted=False, limit=1000)))
            posts = db.publications_count_since(local_midnight_utc(utcnow()))
            return [
                ondemand.status_line(),
                f"🧠 IA: {evals} avaliações hoje · "
                f"{'⚠️ fora do ar, tentando de novo' if pipeline.ai_down else 'Gemini ✅'}",
                f"📥 Aprovação: {waiting} aguardando",
                f"📤 Hoje: {posts} publicados",
            ]

        panel = Panel(runtime, [p.name for p in publishers], panel_status)
        if bot is not None:
            bot.attach_panel(panel)

        await media_server.start()
        jobs = {
            "pipeline": pipeline.run,
            "ondemand": ondemand.run,
            "scheduler": scheduler.run,
            "approval": desk.run,
            "notifier": monitor.run_sender,
            "cleanup": lambda: cleanup_loop(media_dir, protected=db.protected_media_ids),
        }
        if bot is not None:
            jobs["discord"] = bot.run
            jobs["panel"] = bot.run_panel

        plats = ", ".join(p.name for p in publishers) or "nenhuma"
        # Vai para a fila de avisos; o sender entrega quando o Discord estiver pronto.
        await monitor.info(f"🤖 **liveclips iniciado** · plataformas configuradas: {plats}")
        await asyncio.gather(*(supervise(n, f, monitor.alert) for n, f in jobs.items()))


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
