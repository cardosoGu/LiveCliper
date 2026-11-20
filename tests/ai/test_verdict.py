import json

from liveclips.ai.prompt import build_prompt
from liveclips.ai.verdict import VERDICT_SCHEMA, parse_verdict

BASE = {"score": 7, "safe": True, "reason": "", "title": "", "caption": "", "hashtags": []}


def test_verdict_defaults_and_subject_validation():
    v = parse_verdict(json.dumps(BASE))
    assert v.series_recap is None and v.subject is None and v.layout == "game"
    v = parse_verdict(json.dumps({**BASE, "layout": "xyz"}))
    assert v.layout == "game"
    v = parse_verdict(json.dumps({**BASE, "layout": "podcast", "series_recap": "CONTINUA",
                                  "subject": {"x": 0.2, "y": 0.1, "w": 0.6, "h": 0.8}}))
    assert v.layout == "podcast" and v.series_recap == "CONTINUA"
    assert v.subject is not None and v.subject.w == 0.6  # rosto grande é aceito
    v = parse_verdict(json.dumps({**BASE, "subject": {"x": 0.8, "y": 0.8, "w": 0.5, "h": 0.5}}))
    assert v.subject is None
    v = parse_verdict(json.dumps({**BASE, "subject": {"x": 0.1, "y": 0.1, "w": 0.01, "h": 0.01}}))
    assert v.subject is None


def test_schema_requires_new_fields():
    props = VERDICT_SCHEMA["properties"]
    assert props["layout"]["enum"] == ["game", "podcast"]
    for k in ("series_recap", "subject", "layout"):
        assert k in props and k in VERDICT_SCHEMA["required"]


def test_prompt_mentions_series():
    meta = {"platform": "twitch", "channel": "x", "viewers": 1, "source": "ondemand",
            "parts": 3, "transcript": "oi"}
    assert "3 partes" in build_prompt(meta)
    assert "partes" not in build_prompt({**meta, "parts": None})
