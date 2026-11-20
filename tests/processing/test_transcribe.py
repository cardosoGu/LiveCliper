from pathlib import Path

import httpx
import respx

from liveclips.processing.transcribe import GROQ_URL, Transcript, transcribe, transcribe_groq


@respx.mock
async def test_transcribe_groq(tmp_path: Path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"fake")
    route = respx.post(GROQ_URL).mock(return_value=httpx.Response(200, json={
        "language": "portuguese",
        "words": [{"word": "oi", "start": 0.1, "end": 0.4}],
        "segments": [{"start": 0, "end": 1, "text": "oi", "extra": 1}]}))
    async with httpx.AsyncClient() as http:
        t = await transcribe_groq(audio, "key", http)
    assert t.segments == [{"start": 0, "end": 1, "text": "oi"}]
    assert t.language == "portuguese"
    assert t.words == [{"word": "oi", "start": 0.1, "end": 0.4}]
    assert b"timestamp_granularities[]" in route.calls[0].request.content
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
