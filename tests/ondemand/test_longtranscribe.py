from pathlib import Path

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
