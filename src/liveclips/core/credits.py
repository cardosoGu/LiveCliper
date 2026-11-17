"""Texto de crédito ao streamer, usado no vídeo, na legenda e nos avisos."""
from __future__ import annotations

import re

_SAFE = re.compile(r"[^A-Za-z0-9_.\-]")


def credit_text(platform: str, channel: str) -> str:
    host = "twitch.tv" if platform == "twitch" else "kick.com"
    return f"{host}/{_SAFE.sub('', channel)}"
