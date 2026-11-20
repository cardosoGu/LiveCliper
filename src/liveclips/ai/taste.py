"""O gosto do admin: os últimos ✅ e ❌ (com o motivo, quando ele escreveu) viram exemplos
no prompt, para a IA calibrar a nota e a escolha dos assuntos."""
from __future__ import annotations

from ..core.db import DB

SNIPPET = 160


def _snippet(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= SNIPPET else text[:SNIPPET].rstrip() + "…"


def _line(r: dict) -> str:
    score = f" (sua nota: {r['ai_score']:.1f})" if r.get("ai_score") is not None else ""
    out = f'- "{r["title"]}"{score}'
    if r.get("note"):
        out += f' — motivo do admin: "{_snippet(r["note"])}"'
    if r.get("transcript"):
        out += f' — fala: "{_snippet(r["transcript"])}"'
    return out


def taste_block(db: DB | None, approved: int = 5, discarded: int = 8) -> str:
    if db is None:
        return ""
    ok = db.recent_feedback("aprovado", approved)
    bad = db.recent_feedback("descartado", discarded)
    if not ok and not bad:
        return ""
    lines = ["GOSTO DO ADMIN — decisões recentes de quem aprova os cortes. Use para calibrar:",
             "pareceu com um descartado, baixe a nota (ou não escolha); pareceu com um aprovado, suba.",
             "Os motivos escritos pelo admin valem como regra."]
    if ok:
        lines += ["APROVADOS:", *(_line(r) for r in ok)]
    if bad:
        lines += ["DESCARTADOS:", *(_line(r) for r in bad)]
    return "\n".join(lines)
