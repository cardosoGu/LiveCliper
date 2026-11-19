from liveclips.control.panel import Panel
from liveclips.control.runtime import RuntimeSettings
from liveclips.core.config import load_settings


def make(db, configured=("instagram", "tiktok"), **env):
    rt = RuntimeSettings(db, load_settings(env))
    panel = Panel(rt, list(configured), lambda: ["📥 Aprovação: 2 aguardando"])
    return panel, rt


def test_state_reflects_runtime(db):
    panel, rt = make(db)
    s = panel.state()
    assert not s.paused
    assert s.min_score == 6.0 and s.interval == 45 and s.limit == 8
    assert ("youtube", True, False) in s.platforms and ("tiktok", True, True) in s.platforms
    assert "Automático" not in s.text and "2 aguardando" in s.text


async def test_toggle_pause(db):
    panel, rt = make(db)
    await panel.handle("pause", [])
    assert rt.paused() is True
    await panel.handle("pause", [])
    assert rt.paused() is False


async def test_score_pace_and_platforms(db):
    panel, rt = make(db)
    await panel.handle("score", ["7"])
    await panel.handle("pace", ["30|12"])
    await panel.handle("plat-tiktok", [])
    assert rt.min_score() == 7.0
    assert rt.post_interval_min() == 30 and rt.post_max_per_day() == 12
    assert rt.platform_enabled("tiktok") is False
    assert "não configurado" in await panel.handle("plat-youtube", [])
    assert rt.platform_enabled("youtube") is True  # não mexe no que não está configurado


async def test_bad_values_are_explained(db):
    panel, rt = make(db)
    assert "Ação desconhecida" in await panel.handle("explode", [])
    assert "Ação desconhecida" in await panel.handle("auto", [])
    assert "inválid" in (await panel.handle("score", ["muito"])).lower()
