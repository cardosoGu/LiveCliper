"""Texto de crédito ao streamer, usado no vídeo, na legenda e nos avisos."""
from __future__ import annotations

import re

_SAFE = re.compile(r"[^A-Za-z0-9_.\-]")


def credit_text(platform: str, channel: str) -> str:
    name = _SAFE.sub("", channel)
    if platform == "twitch":
        return f"twitch.tv/{name}"
    if platform == "youtube":
        return f"youtube.com/@{name}"
    return f"kick.com/{name}"
