from __future__ import annotations

from ..ai.verdict import Verdict
from ..core.credits import credit_text


def build_caption(verdict: Verdict, platform: str, channel: str, max_len: int = 2200,
                  part: int | None = None, parts: int | None = None) -> str:
    credit = f"🎥 Créditos: {credit_text(platform, channel)}"
    tags = " ".join(verdict.hashtags)
    title = f"({part}/{parts}) {verdict.title}" if part and parts else verdict.title
    body = verdict.caption
    if part and parts:
        body = f"{body}\n\nVeja todas as partes no perfil."
    text = f"{title}\n\n{body}\n\n{credit}\n\n{tags}".strip()
    if len(text) <= max_len:
        return text
    # corta o corpo, nunca os créditos
    tail = f"\n\n{credit}\n\n{tags}"
    return f"{title}\n\n{body}"[: max_len - len(tail) - 1] + "…" + tail
