from __future__ import annotations

from ..evaluator import Verdict
from ..media import credit_text


def build_caption(verdict: Verdict, platform: str, channel: str, max_len: int = 2200) -> str:
    credit = f"🎥 Créditos: {credit_text(platform, channel)}"
    tags = " ".join(verdict.hashtags)
    text = f"{verdict.title}\n\n{verdict.caption}\n\n{credit}\n\n{tags}".strip()
    if len(text) <= max_len:
        return text
    # corta o corpo, nunca os créditos
    tail = f"\n\n{credit}\n\n{tags}"
    return f"{verdict.title}\n\n{verdict.caption}"[: max_len - len(tail) - 1] + "…" + tail
