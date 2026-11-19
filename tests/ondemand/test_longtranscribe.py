import pytest

from liveclips.ondemand.longtranscribe import dump_transcript, load_transcript, transcribe_long
from liveclips.processing.transcribe import Transcript


async def test_chunks_are_offset_and_merged(tmp_path):
    async def split(src, out_dir, chunk_s):
        return [tmp_path / "p0.mp3", tmp_path / "p1.mp3"]

    async def fake(audio, key, http):
        if audio.name == "p0.mp3":
            return Transcript([{"start": 0, "end": 2, "text": "oi gente."}], "pt",
                              [{"word": "oi", "start": 0, "end": 0.5},
                               {"word": "gente.", "start": 0.6, "end": 1.0}])
        return Transcript([{"start": 1, "end": 3, "text": "tchau"}], "pt", [])

    t = await transcribe_long(tmp_path / "a.mp3", "k", None, tmp_path, transcribe_fn=fake,
                              split=split, chunk_s=100)
    assert t.language == "pt"
    assert [s["start"] for s in t.segments] == [0, 101]
    assert t.words[0] == {"word": "oi", "start": 0, "end": 0.5}
    assert t.words[-1]["word"] == "tchau" and t.words[-1]["start"] >= 101
    dump_transcript(t, tmp_path / "t.json")
    again = load_transcript(tmp_path / "t.json")
    assert again.words == t.words and again.language == "pt"


async def test_failed_piece_raises_instead_of_silent_hole(tmp_path):
    async def split(src, out_dir, chunk_s):
        return [tmp_path / "p0.mp3", tmp_path / "p1.mp3"]

    async def fake(audio, key, http):
        if audio.name == "p0.mp3":
            return Transcript([{"start": 0, "end": 2, "text": "oi"}], "pt", [])
        return Transcript()  # transcribe() devolve vazio quando Groq e local falham

    with pytest.raises(RuntimeError, match="pedaço 2"):
        await transcribe_long(tmp_path / "a.mp3", "k", None, tmp_path, transcribe_fn=fake,
                              split=split, chunk_s=100)


async def test_cached_piece_is_reused(tmp_path):
    calls = []

    async def split(src, out_dir, chunk_s):
        return [tmp_path / "p0.mp3", tmp_path / "p1.mp3"]

    async def fake(audio, key, http):
        calls.append(audio.name)
        return Transcript([{"start": 0, "end": 2, "text": "oi"}], "pt", [])

    cached = Transcript([{"start": 0, "end": 2, "text": "cache"}], "pt", [])
    (tmp_path / "parts").mkdir()
    dump_transcript(cached, tmp_path / "parts" / "part_000.json")
    t = await transcribe_long(tmp_path / "a.mp3", "k", None, tmp_path, transcribe_fn=fake,
                              split=split, chunk_s=100)
    assert calls == ["p1.mp3"]
    assert [s["text"] for s in t.segments] == ["cache", "oi"]
    assert (tmp_path / "parts" / "part_001.json").exists()  # o novo pedaço também fica salvo


async def test_split_audio_keeps_piece_json_caches(tmp_path):
    from liveclips.ondemand.longtranscribe import split_audio
    out = tmp_path / "parts"
    out.mkdir()
    (out / "part_000.json").write_text("{}", encoding="utf-8")
    (out / "part_000.mp3").write_bytes(b"old")

    async def run(*cmd, timeout=300):
        (out / "part_000.mp3").write_bytes(b"new")
        return ""

    await split_audio(tmp_path / "a.mp3", out, run=run)
    assert (out / "part_000.json").exists()


async def test_groq_429_becomes_transcription_busy(tmp_path):
    import httpx
    from liveclips.ondemand.longtranscribe import TranscriptionBusy, groq_piece

    def status_error(code):
        req = httpx.Request("POST", "https://x")
        return httpx.HTTPStatusError("e", request=req, response=httpx.Response(code, request=req))

    async def groq_429(audio, key, http):
        raise status_error(429)

    async def groq_500(audio, key, http):
        raise status_error(500)

    with pytest.raises(TranscriptionBusy):
        await groq_piece(tmp_path / "p.mp3", "k", None, groq=groq_429)
    with pytest.raises(httpx.HTTPStatusError):
        await groq_piece(tmp_path / "p.mp3", "k", None, groq=groq_500)

    async def local(audio, key, http):
        return Transcript([], "pt", [])

    assert (await groq_piece(tmp_path / "p.mp3", "", None, fallback=local)).language == "pt"
