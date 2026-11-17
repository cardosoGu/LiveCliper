import json
from pathlib import Path

import pytest

from liveclips.config import load_settings
from liveclips.evaluator import (AIUnavailable, Evaluator, VerdictError, claude_cost_usd,
                                 parse_verdict)

GOOD = json.dumps({"score": 8.5, "safe": True, "reason": "caos", "title": "Explodiu tudo",
                   "caption": "olha isso 😂", "hashtags": ["GTA6", "#gtarp"]})
REFUSAL = json.dumps({"score": 0, "safe": False, "reason": "recusado pelo modelo",
                      "title": "", "caption": "", "hashtags": []})
VIDEO = Path("v.mp4")


def cfg(**env):
    base = {"GEMINI_API_KEY": "g", "ANTHROPIC_API_KEY": "a"}
    base.update(env)
    return load_settings(base)


def test_parse_verdict_normalizes():
    v = parse_verdict(json.dumps({"score": 14, "safe": True, "reason": "", "title": "",
                                  "caption": "", "hashtags": ["GTA6", " ", "#ok"] + ["x"] * 9}))
    assert v.score == 10.0
    assert v.hashtags[:2] == ["#GTA6", "#ok"] and len(v.hashtags) == 8


def test_parse_verdict_rejects_garbage():
    with pytest.raises(VerdictError):
        parse_verdict("isso não é json")


async def test_gemini_ok_skips_claude(db):
    async def gemini(path, prompt):
        return GOOD

    async def claude(path, prompt):
        raise AssertionError("não deveria chamar o Claude")

    v = await Evaluator(cfg(), db, gemini, claude).evaluate(VIDEO, VIDEO, {})
    assert v.score == 8.5 and v.hashtags == ["#GTA6", "#gtarp"]


async def test_gemini_failure_falls_back_to_claude_and_tracks_spend(db):
    async def gemini(path, prompt):
        raise RuntimeError("quota")

    async def claude(path, prompt):
        return GOOD, 0.06

    ev = Evaluator(cfg(), db, gemini, claude)
    await ev.evaluate(VIDEO, VIDEO, {})
    await ev.evaluate(VIDEO, VIDEO, {})
    assert ev.claude_spent_today() == pytest.approx(0.12)


async def test_budget_exhausted_raises(db):
    async def gemini(path, prompt):
        raise RuntimeError("quota")

    async def claude(path, prompt):
        return GOOD, 5.0

    ev = Evaluator(cfg(CLAUDE_DAILY_BUDGET_USD="3"), db, gemini, claude)
    await ev.evaluate(VIDEO, VIDEO, {})  # gasta 5 > 3
    with pytest.raises(AIUnavailable):
        await ev.evaluate(VIDEO, VIDEO, {})


async def test_no_keys_raises(db):
    with pytest.raises(AIUnavailable):
        await Evaluator(load_settings({}), db).evaluate(VIDEO, VIDEO, {})


async def test_refusal_becomes_unsafe(db):
    async def claude(path, prompt):
        return REFUSAL, 0.01

    v = await Evaluator(cfg(GEMINI_API_KEY=""), db, None, claude).evaluate(VIDEO, VIDEO, {})
    assert v.safe is False


def test_claude_cost():
    assert claude_cost_usd("claude-opus-5-5", 1_000_000, 100_000) == pytest.approx(6.0)
    assert claude_cost_usd("claude-haiku-5-5", 1_000_000, 0) == pytest.approx(0.10)
