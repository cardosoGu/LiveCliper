"""Bot do Discord: avisos com botões e slash commands, só para o usuário admin."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import discord
from discord import app_commands

from .monitor import Monitor

log = logging.getLogger(__name__)


class DiscordBot:
    def __init__(self, token: str, channel_id: int, admin_user_id: int):
        self.token = token
        self.channel_id = channel_id
        self.admin_user_id = admin_user_id
        self.client = discord.Client(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self.client)
        self.monitor: Monitor | None = None
        self._ready = asyncio.Event()
        self._channel: discord.abc.Messageable | None = None
        self.client.event(self.on_ready)
        self.client.event(self.on_interaction)

    def attach(self, monitor: Monitor) -> None:
        self.monitor = monitor
        self._register_commands()

    # --- transporte -------------------------------------------------------
    async def _get_channel(self):
        await asyncio.wait_for(self._ready.wait(), timeout=120)
        return self._channel

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
        channel = self.client.get_channel(self.channel_id) or \
            await self.client.fetch_channel(self.channel_id)
        self._channel = channel
        guild = getattr(channel, "guild", None)
        if guild is not None:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        log.info("Discord conectado como %s no canal #%s", self.client.user,
                 getattr(channel, "name", self.channel_id))
        self._ready.set()

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type is not discord.InteractionType.component or self.monitor is None:
            return
        custom_id = (interaction.data or {}).get("custom_id", "")
        if interaction.user.id != self.admin_user_id:
            await interaction.response.send_message("Sem permissão.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        result = await self.monitor.handle_action(custom_id)
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

    async def run(self) -> None:
        await self.client.start(self.token)
