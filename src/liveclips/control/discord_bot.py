"""Bot do Discord: avisos com botões e slash commands, só para o usuário admin."""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import discord
from discord import app_commands

from ..ai.verdict import Verdict
from ..core.db import DB
from ..core.models import State
from .approval import ApprovalDesk, approval_custom_id, parse_custom_id
from .channels import ensure_channels
from .monitor import Monitor
from .panel import (INTERVAL_OPTIONS, LIMIT_OPTIONS, PLATFORM_LABEL as PANEL_LABEL,
                    SCORE_OPTIONS, Panel, PanelState)

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://\S+")


def parse_link_message(text: str) -> tuple[str, str] | None:
    m = URL_RE.search(text or "")
    if not m:
        return None
    rest = (text[:m.start()].rstrip().removesuffix("<") + " " + text[m.end():]).strip()
    url = m.group(0).strip("<>").rstrip("),.>")
    return url, " ".join(rest.split())[:500]


def build_panel_view(s: PanelState) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="▶ Retomar" if s.paused else "⏸ Pausar",
                                    custom_id="panel:pause", style=discord.ButtonStyle.secondary,
                                    row=0))
    view.add_item(discord.ui.Select(
        custom_id="panel:score", row=1,
        options=[discord.SelectOption(label=f"nota mínima {v}", value=str(v),
                                      default=float(v) == s.min_score) for v in SCORE_OPTIONS]))
    view.add_item(discord.ui.Select(
        custom_id="panel:pace", row=2,
        options=[discord.SelectOption(label=f"a cada {i} min · até {lim}/dia", value=f"{i}|{lim}",
                                      default=(i, lim) == (s.interval, s.limit))
                 for i in INTERVAL_OPTIONS for lim in LIMIT_OPTIONS]))
    return view


def build_platforms_view(s: PanelState) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for name, enabled, configured in s.platforms:
        view.add_item(discord.ui.Button(
            label=f"{PANEL_LABEL[name]} {'✅' if enabled and configured else '⚪'}",
            custom_id=f"panel:plat-{name}", disabled=not configured,
            style=discord.ButtonStyle.success if enabled and configured
            else discord.ButtonStyle.secondary))
    return view


class DiscordBot:
    def __init__(self, token: str, channel_id: int, admin_user_id: int, db: DB,
                 channel_ids: dict[str, int] | None = None):
        self.channel_ids = channel_ids or {}
        self.token = token
        self.channel_id = channel_id
        self.admin_user_id = admin_user_id
        self.monitor: Monitor | None = None
        self._read_messages = True  # vira False se o Discord recusar o Message Content Intent
        self.db = db
        self.channels: dict = {}
        self.desk: ApprovalDesk | None = None
        self.panel: Panel | None = None
        self._ready = asyncio.Event()
        self._channel: discord.abc.Messageable | None = None
        self._panel_lock = asyncio.Lock()  # refresh_panel concorrente duplicaria a mensagem
        self._warned_no_panel = False
        self.ondemand = None
        self._build_client()

    def _build_client(self) -> None:
        """Cria um discord.Client novo (um client fechado não conecta de novo: a sessão
        HTTP dele fica fechada) e registra eventos e comandos nele."""
        intents = discord.Intents.default()
        intents.message_content = self._read_messages
        self.client = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.client)
        self.client.event(self.on_ready)
        self.client.event(self.on_interaction)
        self.client.event(self.on_message)
        if self.monitor is not None:
            self._register_commands()

    def attach_ondemand(self, runner) -> None:
        self.ondemand = runner

    async def on_message(self, message: discord.Message) -> None:
        links = self.channels.get("gerar-clipes")
        if (self.ondemand is None or links is None or message.author.bot
                or message.channel.id != links.id
                or message.author.id != self.admin_user_id):
            return
        parsed = parse_link_message(message.content)
        if parsed is None:
            return
        url, instructions = parsed
        job_id = self.ondemand.enqueue(url, instructions, message.channel.id, message.id)
        try:
            await message.add_reaction("⏳")
            thread = await message.create_thread(name=f"Cortes #{job_id}")
            pos = self.ondemand.queue_position(job_id)
            status = await thread.send(f"⏳ na fila (posição {pos})" if pos > 1
                                       else "⏳ começando…")
            self.db.update_job(job_id, thread_id=thread.id, status_message_id=status.id)
        except discord.HTTPException as e:
            log.warning("não deu para abrir a thread do job %s: %s", job_id, e)

    def attach(self, monitor: Monitor) -> None:
        self.monitor = monitor
        self._register_commands()

    def attach_desk(self, desk: ApprovalDesk) -> None:
        self.desk = desk

    def attach_panel(self, panel: Panel) -> None:
        self.panel = panel

    async def _panel_message(self, key: str, content: str, view: discord.ui.View) -> None:
        channel = self.channels.get("painel")
        if channel is None:
            if not self._warned_no_panel:
                self._warned_no_panel = True
                log.warning("canal #painel não encontrado: painel não será publicado")
            return
        saved = self.db.kv_get(key)
        if saved:
            try:
                msg = await channel.fetch_message(int(saved))
                await msg.edit(content=content[:2000], view=view)
                return
            except discord.NotFound:
                pass  # apagaram a mensagem: cria de novo
        msg = await channel.send(content[:2000], view=view)
        self.db.kv_set(key, str(msg.id))

    async def refresh_panel(self) -> None:
        if self.panel is None:
            return
        await asyncio.wait_for(self._ready.wait(), timeout=120)
        async with self._panel_lock:
            s = self.panel.state()
            failed = None
            for key, content, build in (
                    ("panel:message_id", s.text, build_panel_view),
                    ("panel:platforms_message_id", "**Plataformas**", build_platforms_view)):
                try:  # um erro numa mensagem não impede a outra
                    await self._panel_message(key, content, build(s))
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.exception("mensagem do painel %s não atualizada", key)
                    failed = failed or e
            if failed is not None:
                raise failed

    async def run_panel(self) -> None:
        while True:
            try:
                await self.refresh_panel()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("painel não atualizado")
            await asyncio.sleep(60)

    # --- transporte -------------------------------------------------------
    async def _get_channel(self):
        await asyncio.wait_for(self._ready.wait(), timeout=120)
        return self._channel

    async def approval_channel(self):
        await asyncio.wait_for(self._ready.wait(), timeout=120)
        return self.channels.get("aprovação") or self._channel

    async def send(self, text: str, buttons: list[tuple[str, str]] | None = None) -> None:
        channel = await self._get_channel()
        view = None
        if buttons:
            view = discord.ui.View(timeout=None)
            for label, custom_id in buttons:
                view.add_item(discord.ui.Button(label=label, custom_id=custom_id[:100],
                                                style=discord.ButtonStyle.secondary))
        await channel.send(text[:2000], view=view)

    async def send_file(self, path: Path, text: str) -> None:
        channel = await self._get_channel()
        await channel.send(text[:2000], file=discord.File(str(path)))

    # --- eventos ----------------------------------------------------------
    async def on_ready(self) -> None:
        try:
            configured = (self.client.get_channel(self.channel_id)
                          or await self.client.fetch_channel(self.channel_id))
            self._channel = configured
            guild = getattr(configured, "guild", None)
            if guild is not None:
                try:
                    self.channels = await ensure_channels(guild, self.db, self.channel_ids)
                    self._channel = self.channels["logs"]  # avisos vão para o #logs
                except discord.Forbidden:
                    log.warning("sem permissão para criar canais: tudo fica em #%s",
                                getattr(configured, "name", self.channel_id))
                except discord.HTTPException as e:
                    log.error("falha ao criar os canais: %s", e)
                try:
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                except discord.HTTPException as e:
                    log.error("falha ao sincronizar os comandos: %s", e)
            log.info("Discord conectado como %s; canais: %s", self.client.user,
                     ", ".join(self.channels) or getattr(configured, "name", self.channel_id))
        except Exception:
            log.exception("on_ready falhou")
        finally:
            self._ready.set()  # nunca deixa cards e avisos esperando 120 s

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type is not discord.InteractionType.component:
            return  # modais e slash commands têm seus próprios handlers
        custom_id = (interaction.data or {}).get("custom_id", "")
        if interaction.user.id != self.admin_user_id:
            await interaction.response.send_message("Só o admin pode usar isso.",
                                                    ephemeral=True)
            return
        if custom_id.startswith("panel:") and self.panel is not None:
            action = custom_id.removeprefix("panel:")
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                msg = await self.panel.handle(action, list((interaction.data or {}).get("values", [])))
            except Exception as e:
                log.exception("ação do painel %s falhou", action)
                msg = f"Erro: {e}"
            else:
                try:
                    await self.refresh_panel()
                except Exception:
                    log.exception("painel não atualizado após %s", action)
                    msg += " (painel não atualizado)"
            await interaction.followup.send(msg[:2000], ephemeral=True)
            return
        parsed = parse_custom_id(custom_id)
        if parsed is not None and self.desk is not None:
            action, cid = parsed
            if action == "edit":
                c = self.db.get(cid)
                if c is None or c.state is not State.AWAITING_APPROVAL:
                    await interaction.response.send_message(
                        "Esse clipe não está mais aguardando aprovação.", ephemeral=True)
                    return
                try:
                    v = Verdict.model_validate_json(c.ai_json)
                    await interaction.response.send_modal(
                        EditModal(self.desk, cid, v.title, v.caption))
                except Exception as e:
                    log.exception("editar clipe %s falhou", cid)
                    await interaction.response.send_message(f"Erro: {e}"[:2000],
                                                            ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                result = await self.desk.handle(action, cid)
            except Exception as e:
                log.exception("ação %s do clipe %s falhou", action, cid)
                result = f"Erro: {e}"
            await interaction.followup.send(result[:2000], ephemeral=True)
            return
        if self.monitor is None:
            return
        await interaction.response.defer(thinking=True)
        try:
            result = await self.monitor.handle_action(custom_id)
        except Exception as e:
            log.exception("ação %s falhou", custom_id)
            result = f"Erro: {e}"
        await interaction.followup.send(result[:2000])

    def _register_commands(self) -> None:
        bot = self

        async def reply(interaction: discord.Interaction, name: str, arg: str | None = None):
            if interaction.user.id != bot.admin_user_id:
                await interaction.response.send_message("Sem permissão.", ephemeral=True)
                return
            await interaction.response.send_message(bot.monitor.handle_command(name, arg)[:2000])

        @self.tree.command(name="pause", description="Pausa todas as publicações")
        async def pause(interaction: discord.Interaction):
            await reply(interaction, "pause")

        @self.tree.command(name="resume", description="Retoma as publicações")
        async def resume(interaction: discord.Interaction):
            await reply(interaction, "resume")

        @self.tree.command(name="status", description="Mostra o estado do sistema")
        async def status(interaction: discord.Interaction):
            await reply(interaction, "status")

        @self.tree.command(name="min_score", description="Define a nota mínima da IA (0 a 10)")
        @app_commands.describe(nota="ex.: 7.5")
        async def min_score(interaction: discord.Interaction, nota: float):
            await reply(interaction, "min_score", str(nota))

        @self.tree.command(name="plataforma", description="Liga ou desliga uma plataforma")
        @app_commands.describe(nome="instagram, youtube ou tiktok", estado="on ou off")
        async def plataforma(interaction: discord.Interaction, nome: str, estado: str):
            await reply(interaction, "plataforma", f"{nome} {estado}")

    async def run(self) -> None:
        if self.client.is_closed():  # o supervisor reiniciou depois de uma queda
            self._build_client()
        try:
            await self.client.start(self.token)
        except discord.PrivilegedIntentsRequired:
            log.error("ligue 'Message Content Intent' no Discord Developer Portal para usar "
                      "o #gerar-clipes; seguindo sem ler mensagens")
            try:
                await self.client.close()
            except Exception:
                pass
            self._read_messages = False
            self._build_client()
            await self.client.start(self.token)


class EditModal(discord.ui.Modal, title="Editar clipe"):
    def __init__(self, desk: ApprovalDesk, candidate_id: int, hook: str, caption: str):
        super().__init__(timeout=600)
        self.desk = desk
        self.candidate_id = candidate_id
        self.hook = discord.ui.TextInput(label="Título-gancho", default=hook[:100],
                                         max_length=100)
        self.caption_input = discord.ui.TextInput(label="Legenda", default=caption[:1000],
                                                  style=discord.TextStyle.paragraph,
                                                  max_length=1000, required=False)
        self.add_item(self.hook)
        self.add_item(self.caption_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            msg = await self.desk.edit(self.candidate_id, str(self.hook.value),
                                       str(self.caption_input.value))
        except Exception as e:
            log.exception("salvar edição do clipe %s falhou", self.candidate_id)
            msg = f"Erro ao salvar: {e}"
        await interaction.followup.send(msg[:2000], ephemeral=True)


_BUTTONS = (("✅ Aprovar", "approve", discord.ButtonStyle.success),
            ("⚡ Agora", "now", discord.ButtonStyle.primary),
            ("✏️ Editar", "edit", discord.ButtonStyle.secondary),
            ("❌ Descartar", "discard", discord.ButtonStyle.danger))


class DiscordJobNotifier:
    """Progresso do sob demanda na thread da mensagem original."""

    def __init__(self, bot: DiscordBot):
        self.bot = bot
        self._last: dict[int, str] = {}

    async def _channel(self, channel_id):
        return (self.bot.client.get_channel(channel_id)
                or await self.bot.client.fetch_channel(channel_id))

    async def progress(self, job, text):
        if not job.get("thread_id") or not job.get("status_message_id"):
            return
        if self._last.get(job["id"]) == text:
            return
        await asyncio.wait_for(self.bot._ready.wait(), timeout=120)
        thread = await self._channel(job["thread_id"])
        msg = await thread.fetch_message(job["status_message_id"])
        await msg.edit(content=text[:2000])
        self._last[job["id"]] = text

    async def finished(self, job, ok, text):
        try:
            if job.get("thread_id"):
                thread = await self._channel(job["thread_id"])
                if getattr(thread, "archived", False):
                    try:
                        await thread.edit(archived=False)
                    except Exception:
                        pass
            await self.progress(job, ("✅ " if ok else "❌ ") + text)
        except Exception as e:  # o aviso na thread não pode impedir a troca da reação
            log.warning("status final do job %s não editado: %s", job.get("id"), e)
        if job.get("channel_id") and job.get("message_id"):
            channel = await self._channel(job["channel_id"])
            msg = await channel.fetch_message(job["message_id"])
            try:
                await msg.remove_reaction("⏳", self.bot.client.user)
            except discord.HTTPException:
                pass
            await msg.add_reaction("✅" if ok else "❌")
        self._last.pop(job["id"], None)


class DiscordApprovalTransport:
    """Cards do #aprovação. Os botões são tratados em DiscordBot.on_interaction pelo
    custom_id, então continuam funcionando depois de um reinício do app."""

    def __init__(self, bot: DiscordBot):
        self.bot = bot

    @staticmethod
    def _view(candidate_id: int) -> discord.ui.View:
        view = discord.ui.View(timeout=None)
        for label, action, style in _BUTTONS:
            view.add_item(discord.ui.Button(label=label, style=style,
                                            custom_id=approval_custom_id(action, candidate_id)))
        return view

    @staticmethod
    def _retry_view(candidate_id: int, platforms: tuple[str, ...]) -> discord.ui.View:
        view = discord.ui.View(timeout=None)
        for name in platforms:
            view.add_item(discord.ui.Button(
                label=f"🔁 {PANEL_LABEL.get(name, name)}", style=discord.ButtonStyle.secondary,
                custom_id=approval_custom_id(f"retry-{name}", candidate_id)))
        return view

    async def post(self, text, video, link, candidate_id, buttons):
        channel = await self.bot.approval_channel()
        body = (f"🔗 vídeo: {link}\n" if link else "") + text  # o link nunca é cortado
        kwargs = {}
        if video is not None:
            kwargs["file"] = discord.File(str(video))
        if buttons:
            kwargs["view"] = self._view(candidate_id)
        msg = await channel.send(body[:2000], **kwargs)
        return msg.id

    async def edit(self, message_id, candidate_id, text, buttons, retry=()):
        channel = await self.bot.approval_channel()
        msg = await channel.fetch_message(message_id)
        if buttons:
            view = self._view(candidate_id)
        elif retry:
            view = self._retry_view(candidate_id, retry)
        else:
            view = None
        await msg.edit(content=text[:2000], view=view)
