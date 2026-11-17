import json
from pathlib import Path

import pytest

from liveclips.core.config import load_settings
from liveclips.ai.claude import claude_cost_usd
from liveclips.ai.evaluator import AIUnavailable, Evaluator
from liveclips.ai.verdict import VerdictError, parse_verdict

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


async def test_gemini_tries_next_model_when_one_fails(tmp_path, monkeypatch):
    from liveclips.ai import gemini

    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake video")
    tried = []

    class Models:
        def generate_content(self, model, contents, config):
            tried.append(model)
            if model == "sobrecarregado":
                raise RuntimeError("503 UNAVAILABLE")
            return type("R", (), {"text": GOOD})()

    monkeypatch.setattr(gemini, "_client", lambda key: type("C", (), {"models": Models()})())
    c = cfg(GEMINI_MODEL="sobrecarregado, bom")
    assert await gemini.gemini_evaluate(c, video, "p") == GOOD
    assert tried == ["sobrecarregado", "bom"]


async def test_gemini_all_models_failing_raises(tmp_path, monkeypatch):
    from liveclips.ai import gemini

    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")

    class Models:
        def generate_content(self, model, contents, config):
            raise RuntimeError("503")

    monkeypatch.setattr(gemini, "_client", lambda key: type("C", (), {"models": Models()})())
    with pytest.raises(RuntimeError, match="todos os modelos"):
        await gemini.gemini_evaluate(cfg(GEMINI_MODEL="a,b"), video, "p")


async def test_gemini_tries_every_key_before_next_model(tmp_path, monkeypatch):
    from liveclips.ai import gemini

    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    tried = []

    def client(key):
        class Models:
            def generate_content(self, model, contents, config):
                tried.append((model, key))
                if (model, key) != ("m2", "k1"):
                    raise RuntimeError("429")
                return type("R", (), {"text": GOOD})()
        return type("C", (), {"models": Models()})()

    monkeypatch.setattr(gemini, "_client", client)
    c = load_settings({"GEMINI_API_KEY1": "k1", "GEMINI_API_KEY2": "k2", "GEMINI_API_KEY3": "k3",
                       "GEMINI_MODEL": "m1,m2"})
    assert await gemini.gemini_evaluate(c, video, "p") == GOOD
    assert tried == [("m1", "k1"), ("m1", "k2"), ("m1", "k3"), ("m2", "k1")]


def _with_facecam(fc):
    return json.dumps({"score": 8, "safe": True, "reason": "", "title": "", "caption": "",
                       "hashtags": [], "facecam": fc})


def test_facecam_valid_is_kept():
    v = parse_verdict(_with_facecam({"x": 0.0, "y": 0.7, "w": 0.25, "h": 0.3}))
    assert (v.facecam.x, v.facecam.y, v.facecam.w, v.facecam.h) == (0.0, 0.7, 0.25, 0.3)


def test_facecam_missing_or_null_is_none():
    assert parse_verdict(GOOD).facecam is None  # vereditos antigos (sem o campo) continuam válidos
    assert parse_verdict(_with_facecam(None)).facecam is None


@pytest.mark.parametrize("fc", [
    {"x": 0.4, "y": 0.4, "w": 0.05, "h": 0.1},   # 0,5% da área: pequeno demais
    {"x": 0.0, "y": 0.0, "w": 0.8, "h": 0.6},    # 48%: grande demais
    {"x": 0.8, "y": 0.5, "w": 0.4, "h": 0.4},    # passa da borda direita
    {"x": -0.2, "y": 0.1, "w": 0.3, "h": 0.3},   # começa fora do quadro
    {"x": 0.1, "y": 0.1, "w": 0.0, "h": 0.3},    # largura zero
])
def test_facecam_invalid_becomes_none(fc):
    assert parse_verdict(_with_facecam(fc)).facecam is None


def test_facecam_slightly_out_of_bounds_is_trimmed():
    v = parse_verdict(_with_facecam({"x": 0.76, "y": -0.01, "w": 0.25, "h": 0.3}))
    assert v.facecam.y == 0 and abs(v.facecam.x + v.facecam.w - 1.0) < 1e-9


def test_claude_schema_requires_nullable_facecam():
    from liveclips.ai.verdict import VERDICT_SCHEMA

    assert "facecam" in VERDICT_SCHEMA["required"]
    types = [s.get("type") for s in VERDICT_SCHEMA["properties"]["facecam"]["anyOf"]]
    assert types == ["object", "null"]


def test_prompt_asks_for_facecam_and_short_hook():
    from liveclips.ai.prompt import SYSTEM_PROMPT

    assert "facecam" in SYSTEM_PROMPT and "null" in SYSTEM_PROMPT
    assert "35 caracteres" in SYSTEM_PROMPT and "PERGUNTA" in SYSTEM_PROMPT
