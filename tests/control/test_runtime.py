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
