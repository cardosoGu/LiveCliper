from pathlib import Path

import httpx
import respx

from liveclips.processing.transcribe import (GROQ_URL, Transcript, segments_to_srt, transcribe,
                                             transcribe_groq)


def test_segments_to_srt():
    srt = segments_to_srt([
        {"start": 0.0, "end": 1.5, "text": " olha isso "},
        {"start": 1.5, "end": 2.0, "text": "  "},
        {"start": 61.25, "end": 3725.0, "text": "kkkk"},
    ])
    assert srt == (
        "1\n00:00:00,000 --> 00:00:01,500\nolha isso\n\n"
        "2\n00:01:01,250 --> 01:02:05,000\nkkkk\n"
    )


@respx.mock
async def test_transcribe_groq(tmp_path: Path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"fake")
    route = respx.post(GROQ_URL).mock(return_value=httpx.Response(200, json={
        "language": "portuguese",
        "segments": [{"start": 0, "end": 1, "text": "oi", "extra": 1}]}))
    async with httpx.AsyncClient() as http:
        t = await transcribe_groq(audio, "key", http)
    assert t.segments == [{"start": 0, "end": 1, "text": "oi"}]
    assert t.language == "portuguese"
    assert route.calls[0].request.headers["Authorization"] == "Bearer key"
    assert b'name="language"' not in route.calls[0].request.content  # idioma não é forçado


async def test_transcribe_fallback_order(tmp_path: Path):
    calls = []

    async def bad_groq(*a):
        calls.append("groq")
        raise RuntimeError("quota")

    async def ok_local(audio):
        calls.append("local")
        return Transcript([{"start": 0, "end": 1, "text": "x"}], "pt")

    async def bad_local(audio):
        raise RuntimeError("sem modelo")

    assert (await transcribe(tmp_path, "k", None, bad_groq, ok_local)).text == "x"
    assert calls == ["groq", "local"]
    empty = await transcribe(tmp_path, "", None, bad_groq, bad_local)
    assert empty.segments == [] and empty.language is None


def test_language_detection_needs_enough_speech():
    seg = lambda text: [{"start": 0, "end": 1, "text": text}]
    assert Transcript(seg("oh my god he fell off the building"), "english").is_clearly_not_portuguese()
    assert not Transcript(seg("mano ele caiu do prédio kkkk"), "portuguese").is_clearly_not_portuguese()
    assert not Transcript(seg("pt"), "pt").is_clearly_not_portuguese()
    assert not Transcript(seg("oh no"), "english").is_clearly_not_portuguese()  # pouca fala
    assert not Transcript([], None).is_clearly_not_portuguese()
