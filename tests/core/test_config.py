import pytest

from liveclips.core.config import load_settings


def test_defaults_when_env_empty():
    s = load_settings({})
    assert s.port == 8080
    assert s.data_dir == "/data"
    assert s.twitch_game_name == "Grand Theft Auto VI"
    assert s.ai_min_score == 8.0
    assert s.claude_model == "claude-opus-5-5"
    assert s.gemini_model == "gemini-3.5-flash,gemini-3.8-flash"
    assert s.tiktok_mode == "off" and s.yt_mode == "off"
    assert s.dry_run is False
    assert s.tunnel_metrics_url == "http://cloudflared:2000/quicktunnel"


def test_overrides_from_env():
    s = load_settings({
        "PORT": "9000", "DRY_RUN": "1", "AI_MIN_SCORE": "8.5",
        "TIKTOK_MODE": "PRIVATE", "PUBLIC_BASE_URL": "https://x.example/",
        "DISCORD_CHANNEL_ID": "123",
    })
    assert s.port == 9000
    assert s.dry_run is True
    assert s.ai_min_score == 8.5
    assert s.tiktok_mode == "private"
    assert s.public_base_url == "https://x.example"
    assert s.discord_channel_id == 123


def test_invalid_mode_raises():
    with pytest.raises(ValueError):
        load_settings({"YT_MODE": "sometimes"})
