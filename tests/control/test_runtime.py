import pytest

from liveclips.control.runtime import RuntimeSettings
from liveclips.core.config import load_settings


def test_defaults_come_from_env(db):
    rt = RuntimeSettings(db, load_settings({"AI_MIN_SCORE": "7", "POST_MIN_INTERVAL_MIN": "30",
                                            "POST_MAX_PER_DAY": "12"}))
    assert rt.min_score() == 7.0
    assert rt.post_interval_min() == 30 and rt.post_max_per_day() == 12
    assert rt.paused() is False
    assert rt.platform_enabled("tiktok") is True


def test_dry_run_starts_with_platforms_off(db):
    rt = RuntimeSettings(db, load_settings({"DRY_RUN": "1"}))
    assert not any(rt.platform_enabled(p) for p in ("instagram", "youtube", "tiktok"))


def test_changes_are_saved_and_win_over_env(db):
    cfg = load_settings({"DRY_RUN": "1"})
    rt = RuntimeSettings(db, cfg)
    rt.set_platform("tiktok", True)
    rt.set_min_score(6.5)
    rt.set_post_interval_min(15)
    rt.set_post_max_per_day(4)
    rt.set_paused(True)
    again = RuntimeSettings(db, cfg)  # simula um reinício
    assert again.platform_enabled("tiktok") and not again.platform_enabled("youtube")
    assert again.min_score() == 6.5
    assert again.post_interval_min() == 15 and again.post_max_per_day() == 4
    assert again.paused() is True


def test_invalid_values_are_refused(db):
    rt = RuntimeSettings(db, load_settings({}))
    with pytest.raises(ValueError):
        rt.set_min_score(11)
    with pytest.raises(ValueError):
        rt.set_platform("orkut", True)


def test_auto_categories_and_viewers_default_from_env(db):
    rt = RuntimeSettings(db, load_settings({"TWITCH_GAME_NAME": "Just Chatting, GTA V",
                                            "MIN_VIEWERS": "1000"}))
    assert rt.auto_on() is True
    assert rt.categories() == ["Just Chatting", "GTA V"]
    assert rt.known_categories() == ["Just Chatting", "GTA V"]
    assert rt.min_viewers() == 1000


def test_auto_categories_and_viewers_are_saved(db):
    cfg = load_settings({"TWITCH_GAME_NAME": "Just Chatting"})
    rt = RuntimeSettings(db, cfg)
    rt.set_auto_on(False)
    rt.add_known_category("Grand Theft Auto VI")
    rt.add_known_category("grand theft auto vi")  # duplicata ignorada
    rt.set_categories(["Grand Theft Auto VI"])
    rt.set_min_viewers(5000)
    again = RuntimeSettings(db, cfg)
    assert again.auto_on() is False
    assert again.known_categories() == ["Just Chatting", "Grand Theft Auto VI"]
    assert again.categories() == ["Grand Theft Auto VI"]
    assert again.min_viewers() == 5000


def test_category_rules(db):
    rt = RuntimeSettings(db, load_settings({"TWITCH_GAME_NAME": "A"}))
    with pytest.raises(ValueError):
        rt.set_categories([])
    with pytest.raises(ValueError):
        rt.set_min_viewers(-1)
    for i in range(24):
        rt.add_known_category(f"Jogo {i}")
    with pytest.raises(ValueError):
        rt.add_known_category("Um a mais")  # 26ª
