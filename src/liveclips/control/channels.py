"""Canais do bot: os informados por ID no .env, ou criados na categoria "Live Clips".

Os IDs ficam lembrados na tabela kv."""
from __future__ import annotations

from ..core.db import DB

CATEGORY = "Live Clips"
CHANNELS = ("painel", "gerar-clipes", "aprovação", "logs")


async def ensure_channels(guild, db: DB, preset: dict[str, int] | None = None) -> dict:
    """Devolve {nome: canal}. Usa os IDs de `preset` (vindos do .env) quando existem no
    servidor; o que faltar é criado (inclusive se alguém apagou um canal)."""
    found: dict = {}
    category = None
    for name in CHANNELS:
        key = f"discord_channel:{name}"
        wanted = (preset or {}).get(name)
        if wanted and guild.get_channel(wanted) is not None:
            db.kv_set(key, str(wanted))
        saved = db.kv_get(key)
        channel = guild.get_channel(int(saved)) if saved else None
        if channel is None:
            if category is None:
                category = next((c for c in guild.categories if c.name == CATEGORY), None) \
                    or await guild.create_category(CATEGORY)
            channel = await guild.create_text_channel(name, category=category)
            db.kv_set(key, str(channel.id))
        found[name] = channel
    return found
