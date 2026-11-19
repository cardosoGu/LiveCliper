import pytest

from liveclips.ai.evaluator import AIUnavailable
from liveclips.ai.text import ask_json
from liveclips.core.config import load_settings
from liveclips.ondemand.topics import (TopicCut, TopicsAnswer, build_topics_prompt,
                                       choose_topics, fmt_time, parse_topics)

WORDS = [{"word": "olha.", "start": 100.0, "end": 100.5},
         {"word": "que", "start": 200.0, "end": 200.3},
         {"word": "história.", "start": 200.4, "end": 201.0}]


def test_fmt_time():
    assert fmt_time(75) == "1:15" and fmt_time(3725) == "1:02:05"


def test_prompt_has_windows_per_peak_and_instructions():
    p = build_topics_prompt("Podpah #1", WORDS, [110, 210], "foca no convidado")
    assert "Pico 0" in p and "Pico 1" in p and "foca no convidado" in p
    assert "[1:40] olha." in p and "[3:20] que história." in p


def test_parse_topics_merges_overlaps_and_drops_rejected():
    ans = TopicsAnswer(cortes=[
        TopicCut(pico=0, manter=True, inicio=90, fim=140, motivo="a"),
        TopicCut(pico=1, manter=True, inicio=130, fim=180, motivo="b"),
        TopicCut(pico=2, manter=False, inicio=300, fim=350, motivo="c"),
        TopicCut(pico=9, manter=True, inicio=0, fim=10, motivo="pico inexistente")])
    [t] = parse_topics(ans, [100, 150, 320])
    assert (t.start, t.end, t.peak) == (90, 180, 100)


def test_peak_outside_range_is_clamped():
    [t] = parse_topics(TopicsAnswer(cortes=[TopicCut(pico=0, manter=True, inicio=200,
                                                     fim=260, motivo="x")]), [100])
    assert 206 <= t.peak <= 257


async def test_choose_topics_uses_the_injected_ai():
    async def ask(cfg, prompt, model_cls, db=None):
        assert model_cls is TopicsAnswer
        return TopicsAnswer(cortes=[TopicCut(pico=0, manter=True, inicio=90, fim=140,
                                             motivo="ok")])

    topics = await choose_topics(load_settings({}), "t", WORDS, [110], "", ask=ask)
    assert len(topics) == 1
    assert await choose_topics(load_settings({}), "t", WORDS, [], "", ask=ask) == []


async def test_ask_json_falls_back_and_raises_when_nothing_works():
    cfg = load_settings({"GEMINI_API_KEY1": "g", "ANTHROPIC_API_KEY": "a"})

    async def bad(cfg, prompt, model_cls):
        raise RuntimeError("429")

    async def good(cfg, prompt, model_cls, db=None):
        return '{"cortes": []}'

    out = await ask_json(cfg, "p", TopicsAnswer, gemini=bad, claude=good)
    assert out.cortes == []

    async def bad_claude(cfg, prompt, model_cls, db=None):
        raise RuntimeError("caiu")

    with pytest.raises(AIUnavailable):
        await ask_json(cfg, "p", TopicsAnswer, gemini=bad, claude=bad_claude)
