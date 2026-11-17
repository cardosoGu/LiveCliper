from pathlib import Path

import httpx
import respx

from liveclips.transcribe import GROQ_URL, segments_to_srt, transcribe, transcribe_groq


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
        "segments": [{"start": 0, "end": 1, "text": "oi", "extra": 1}]}))
    async with httpx.AsyncClient() as http:
        segs = await transcribe_groq(audio, "key", http)
    assert segs == [{"start": 0, "end": 1, "text": "oi"}]
    assert route.calls[0].request.headers["Authorization"] == "Bearer key"


async def test_transcribe_fallback_order(tmp_path: Path):
    calls = []

    async def bad_groq(*a):
        calls.append("groq")
        raise RuntimeError("quota")

    async def ok_local(audio):
        calls.append("local")
        return [{"start": 0, "end": 1, "text": "x"}]

    async def bad_local(audio):
        raise RuntimeError("sem modelo")

    assert await transcribe(tmp_path, "k", None, bad_groq, ok_local) == [
        {"start": 0, "end": 1, "text": "x"}]
    assert calls == ["groq", "local"]
    assert await transcribe(tmp_path, "", None, bad_groq, bad_local) == []
