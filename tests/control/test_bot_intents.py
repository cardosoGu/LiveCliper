import discord

from liveclips.control import discord_bot as mod


class FakeTree:
    def __init__(self, client):
        self.client = client

    def command(self, **kw):
        return lambda f: f


class FakeClient:
    instances: list = []

    def __init__(self, intents):
        self.intents = intents
        self.closed = False
        self.started = False
        FakeClient.instances.append(self)

    def event(self, f):
        return f

    def is_closed(self):
        return self.closed

    async def close(self):
        self.closed = True

    async def start(self, token):
        if self.intents.message_content:
            self.closed = True  # o discord.py fecha a sessão HTTP quando o login falha
            raise discord.PrivilegedIntentsRequired(None)
        self.started = True


async def test_privileged_intent_refused_reconnects_with_a_fresh_client(db, monkeypatch):
    FakeClient.instances = []
    monkeypatch.setattr(mod.discord, "Client", FakeClient)
    monkeypatch.setattr(mod.app_commands, "CommandTree", FakeTree)
    bot = mod.DiscordBot("t", 1, 2, db)
    await bot.run()
    first, second = FakeClient.instances
    assert first.intents.message_content is True and first.closed
    assert second is bot.client and second.started
    assert second.intents.message_content is False
    assert bot.tree.client is second


async def test_restart_after_a_crash_uses_a_new_client(db, monkeypatch):
    FakeClient.instances = []
    monkeypatch.setattr(mod.discord, "Client", FakeClient)
    monkeypatch.setattr(mod.app_commands, "CommandTree", FakeTree)
    bot = mod.DiscordBot("t", 1, 2, db)
    await bot.run()  # cai no fallback e conecta sem ler mensagens
    bot.client.closed = True  # a conexão caiu; o supervisor chama run() de novo
    await bot.run()
    assert len(FakeClient.instances) == 3
    assert FakeClient.instances[-1].started
    assert FakeClient.instances[-1].intents.message_content is False  # lembra do fallback
