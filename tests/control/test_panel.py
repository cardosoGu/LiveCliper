from liveclips.control.panel import Panel
from liveclips.control.runtime import RuntimeSettings
from liveclips.core.config import load_settings


class FakeAuto:
    def __init__(self):
        self.applied = 0
        self.running = True

    async def apply(self):
        self.applied += 1
        return "aplicado"

    def status_line(self):
        return "📡 Automático: LIGADO · 1 categorias"


def make(db, configured=("instagram", "tiktok"), **env):
    rt = RuntimeSettings(db, load_settings({"TWITCH_GAME_NAME": "Just Chatting", **env}))
    auto = FakeAuto()

    async def resolve(names):
        return {"Grand Theft Auto VI": "1"} if names == ["gta vi"] else {}

    panel = Panel(rt, auto, list(configured), lambda: ["📥 Aprovação: 2 aguardando"], resolve)
    return panel, rt, auto


def test_state_reflects_runtime(db):
    panel, rt, _ = make(db)
    s = panel.state()
    assert s.auto_on and not s.paused
    assert s.selected == ["Just Chatting"] and s.known == ["Just Chatting"]
    assert s.min_score == 6.0 and s.interval == 45 and s.limit == 8
    assert ("youtube", True, False) in s.platforms and ("tiktok", True, True) in s.platforms
    assert "Automático: LIGADO" in s.text and "2 aguardando" in s.text


async def test_toggle_auto_and_pause(db):
    panel, rt, auto = make(db)
    assert await panel.handle("auto", []) == "aplicado"  # a frase vem do AutoController
    assert rt.auto_on() is False and auto.applied == 1
    await panel.handle("pause", [])
    assert rt.paused() is True
    await panel.handle("pause", [])
    assert rt.paused() is False


async def test_categories_and_viewers_reapply_auto(db):
    panel, rt, auto = make(db)
    rt.add_known_category("GTA V")
    await panel.handle("cats", ["GTA V"])
    await panel.handle("viewers", ["5000"])
    assert rt.categories() == ["GTA V"] and rt.min_viewers() == 5000
    assert auto.applied == 2


async def test_score_pace_and_platforms(db):
    panel, rt, auto = make(db)
    await panel.handle("score", ["7"])
    await panel.handle("pace", ["30|12"])
    await panel.handle("plat-tiktok", [])
    assert rt.min_score() == 7.0
    assert rt.post_interval_min() == 30 and rt.post_max_per_day() == 12
    assert rt.platform_enabled("tiktok") is False
    assert "não configurado" in await panel.handle("plat-youtube", [])
    assert rt.platform_enabled("youtube") is True  # não mexe no que não está configurado
    assert auto.applied == 0  # nada disso reinicia o automático


async def test_add_category_validates_on_twitch(db):
    panel, rt, auto = make(db)
    msg = await panel.handle("addcat", ["gta vi"])
    assert "Grand Theft Auto VI" in msg
    assert rt.known_categories() == ["Just Chatting", "Grand Theft Auto VI"]
    assert "não existe" in await panel.handle("addcat", ["xyz"])
    assert rt.known_categories() == ["Just Chatting", "Grand Theft Auto VI"]


async def test_bad_values_are_explained(db):
    panel, rt, _ = make(db)
    assert "Ação desconhecida" in await panel.handle("explode", [])
    assert "inválid" in (await panel.handle("viewers", ["muitos"])).lower()
    assert "pelo menos uma" in await panel.handle("cats", [])


def test_state_auto_running_comes_from_controller(db):
    panel, rt, auto = make(db)
    assert panel.state().auto_running is True
    auto.running = False
    s = panel.state()
    assert s.auto_on and s.auto_running is False
