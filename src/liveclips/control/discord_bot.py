"""Bot do Discord: avisos com botões e slash commands, só para o usuário admin."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import discord
from discord import app_commands

from ..ai.verdict import Verdict
from ..core.db import DB
from ..core.models import State
from .approval import ApprovalDesk, approval_custom_id, parse_custom_id
from .channels import ensure_channels
from .monitor import Monitor

log = logging.getLogger(__name__)


class DiscordBot:
    def __init__(self, token: str, channel_id: int, admin_user_id: int, db: DB,
                 channel_ids: dict[str, int] | None = None):
        self.channel_ids = channel_ids or {}
        self.token = token
        self.channel_id = channel_id
        self.admin_user_id = admin_user_id
        self.client = discord.Client(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self.client)
        self.monitor: Monitor | None = None
        self.db = db
        self.channels: dict = {}
        self.desk: ApprovalDesk | None = None
        self._ready = asyncio.Event()
        self._channel: discord.abc.Messageable | None = None
        self.client.event(self.on_ready)
        self.client.event(self.on_interaction)

    def attach(self, monitor: Monitor) -> None:
        self.monitor = monitor
        self._register_commands()

    def attach_desk(self, desk: ApprovalDesk) -> None:
        self.desk = desk

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

        @self.tree.command(name="testar",
                           description="Força um clipe agora e mostra a prévia com a nota da IA "
                                       "(não publica)")
        @app_commands.describe(canal="login de um canal (opcional; padrão: a live mais ativa)")
        async def testar(interaction: discord.Interaction, canal: str | None = None):
            if interaction.user.id != bot.admin_user_id:
                await interaction.response.send_message("Sem permissão.", ephemeral=True)
                return
            await interaction.response.defer(thinking=True)
            result = await bot.monitor.run_test(canal)
            await interaction.followup.send(result[:2000])

        @self.tree.command(name="block", description="Bloqueia um canal")
        @app_commands.describe(canal="login do canal na Twitch/Kick")
        async def block(interaction: discord.Interaction, canal: str):
            await reply(interaction, "block", canal)

        @self.tree.command(name="unblock", description="Desbloqueia um canal")
        @app_commands.describe(canal="login do canal na Twitch/Kick")
        async def unblock(interaction: discord.Interaction, canal: str):
            await reply(interaction, "unblock", canal)

        @self.tree.command(name="min_score", description="Define a nota mínima da IA (0 a 10)")
        @app_commands.describe(nota="ex.: 7.5")
        async def min_score(interaction: discord.Interaction, nota: float):
            await reply(interaction, "min_score", str(nota))

        @self.tree.command(name="plataforma", description="Liga ou desliga uma plataforma")
        @app_commands.describe(nome="instagram, youtube ou tiktok", estado="on ou off")
        async def plataforma(interaction: discord.Interaction, nome: str, estado: str):
            await reply(interaction, "plataforma", f"{nome} {estado}")

    async def run(self) -> None:
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
        await msg.edit(content=text[:2000],
                       view=self._view(candidate_id) if buttons else None)
