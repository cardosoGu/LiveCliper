"""Formato da decisão da IA (igual para Gemini e Claude) e sua validação."""
from __future__ import annotations

from pydantic import BaseModel, ValidationError


class Verdict(BaseModel):
    score: float
    safe: bool
    reason: str
    title: str
    caption: str
    hashtags: list[str]


VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number"},
        "safe": {"type": "boolean"},
        "reason": {"type": "string"},
        "title": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "safe", "reason", "title", "caption", "hashtags"],
    "additionalProperties": False,
}


class VerdictError(Exception):
    pass


def parse_verdict(text: str) -> Verdict:
    try:
        v = Verdict.model_validate_json(text)
    except ValidationError as e:
        raise VerdictError(str(e)) from e
    v.score = max(0.0, min(10.0, v.score))
    tags = [h.strip() for h in v.hashtags if h.strip()]
    v.hashtags = [h if h.startswith("#") else f"#{h}" for h in tags][:8]
    return v


def refusal_verdict() -> Verdict:
    return Verdict(score=0, safe=False, reason="recusado pelo modelo",
                   title="", caption="", hashtags=[])
