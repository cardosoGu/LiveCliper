from __future__ import annotations

from ..ai.verdict import Verdict
from ..core.credits import credit_text


def series_title(title: str, part: int | None, parts: int | None) -> str:
    """Título de uma parte de série; "PARTE n" (sem recap) vira "continuação"."""
    if not (part and parts):
        return title
    if title.startswith("PARTE "):
        title = "continuação"
    return f"({part}/{parts}) {title}"


def build_caption(verdict: Verdict, platform: str, channel: str, max_len: int = 2200,
                  part: int | None = None, parts: int | None = None) -> str:
    credit = f"🎥 Créditos: {credit_text(platform, channel)}"
    tags = " ".join(verdict.hashtags)
    title = series_title(verdict.title, part, parts)
    body = verdict.caption
    if part and parts:
        body = f"{body}\n\nVeja todas as partes no perfil."
    text = f"{title}\n\n{body}\n\n{credit}\n\n{tags}".strip()
    if len(text) <= max_len:
        return text
    # corta o corpo, nunca os créditos
    tail = f"\n\n{credit}\n\n{tags}"
    return f"{title}\n\n{body}"[: max_len - len(tail) - 1] + "…" + tail
