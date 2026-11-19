"""Texto de crédito ao streamer, usado no vídeo, na legenda e nos avisos."""
from __future__ import annotations

import re

_SAFE = re.compile(r"[^A-Za-z0-9_.\-]")


def credit_text(platform: str, channel: str) -> str:
    name = _SAFE.sub("", channel)
    if platform == "twitch":
        return f"twitch.tv/{name}"
    if platform == "outro":
        return name  # domínio do vídeo (ex.: vimeo.com)
    if platform == "youtube":
        path = "/".join(_SAFE.sub("", part) for part in channel.split("/"))
        return f"youtube.com/{path}" if "/" in channel else f"youtube.com/@{name}"
    return f"kick.com/{name}"
