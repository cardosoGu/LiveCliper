"""Formato da decisão da IA (igual para Gemini e Claude) e sua validação."""
from __future__ import annotations

from pydantic import BaseModel, ValidationError

FACECAM_MIN_AREA = 0.01  # 1% do quadro
FACECAM_MAX_AREA = 0.40  # 40% do quadro
_EDGE_TOLERANCE = 0.02   # a IA às vezes passa um pouquinho da borda


class FaceCam(BaseModel):
    """Retângulo da webcam do streamer, normalizado (0 a 1) no vídeo original."""
    x: float
    y: float
    w: float
    h: float


class Verdict(BaseModel):
    score: float
    safe: bool
    reason: str
    title: str
    caption: str
    hashtags: list[str]
    facecam: FaceCam | None = None


_FACECAM_SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "number"} for k in ("x", "y", "w", "h")},
    "required": ["x", "y", "w", "h"],
    "additionalProperties": False,
}

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number"},
        "safe": {"type": "boolean"},
        "reason": {"type": "string"},
        "title": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "facecam": {"anyOf": [_FACECAM_SCHEMA, {"type": "null"}]},
    },
    "required": ["score", "safe", "reason", "title", "caption", "hashtags", "facecam"],
    "additionalProperties": False,
}


class VerdictError(Exception):
    pass


def valid_facecam(fc: FaceCam | None) -> FaceCam | None:
    """Aceita só retângulos plausíveis: dentro do quadro e com 1% a 40% da área.
    Bordas que passam um pouco (≤2%) são aparadas; o resto vira None."""
    if fc is None:
        return None
    t = _EDGE_TOLERANCE
    if (fc.w <= 0 or fc.h <= 0 or fc.x < -t or fc.y < -t
            or fc.x + fc.w > 1 + t or fc.y + fc.h > 1 + t):
        return None
    x, y = max(fc.x, 0.0), max(fc.y, 0.0)
    w = min(fc.w - (x - fc.x), 1.0 - x)
    h = min(fc.h - (y - fc.y), 1.0 - y)
    if not FACECAM_MIN_AREA <= w * h <= FACECAM_MAX_AREA:
        return None
    return FaceCam(x=x, y=y, w=w, h=h)


def parse_verdict(text: str) -> Verdict:
    try:
        v = Verdict.model_validate_json(text)
    except ValidationError as e:
        raise VerdictError(str(e)) from e
    v.score = max(0.0, min(10.0, v.score))
    tags = [h.strip() for h in v.hashtags if h.strip()]
    v.hashtags = [h if h.startswith("#") else f"#{h}" for h in tags][:8]
    v.facecam = valid_facecam(v.facecam)
    return v


def refusal_verdict() -> Verdict:
    return Verdict(score=0, safe=False, reason="recusado pelo modelo",
                   title="", caption="", hashtags=[])
