from liveclips.processing.subtitles import (CANVAS_H, Cue, build_cues, caption_position,
                                            cues_to_ass, words_from_segments)


def w(word, start, end):
    return {"word": word, "start": start, "end": end}


def test_cues_are_short_and_never_overlap():
    words = [w(x, i * 0.3, i * 0.3 + 0.25) for i, x in
             enumerate("com essas glândulas atrofiadas juntamente com o megamiceto a gente".split())]
    cues = build_cues(words)
    assert all(len(c.text.split()) <= 3 and len(c.text) <= 20 for c in cues)
    assert all(a.end <= b.start for a, b in zip(cues, cues[1:]))
    assert " ".join(c.text for c in cues) == " ".join(x["word"] for x in words)


def test_sentence_end_and_pause_split_cues():
    cues = build_cues([w("ótimo.", 0, 0.5), w("com", 0.6, 0.8), w("essas", 0.9, 1.1),
                       w("depois", 2.5, 2.8)])
    assert [c.text for c in cues] == ["ótimo.", "com essas", "depois"]


def test_long_trailing_word_does_not_strand_a_word():
    cues = build_cues([w("esse", 1.2, 1.4), w("da", 1.5, 1.6), w("gravidez.", 1.7, 4.2)])
    assert [c.text for c in cues] == ["esse da gravidez."]


def test_min_duration():
    [c] = build_cues([w("oi", 1.0, 1.05)])
    assert c.end - c.start >= 0.35


def test_words_from_segments_spreads_time():
    words = words_from_segments([{"start": 0, "end": 2, "text": "ab abcd"}])
    assert [x["word"] for x in words] == ["ab", "abcd"]
    assert words[0]["end"] == words[1]["start"] and words[1]["end"] == 2


def test_position_below_landscape_video():
    align, margin = caption_position(1920, 1080)
    assert align == 8 and margin > (CANVAS_H + 608) // 2  # começa abaixo do vídeo


def test_position_falls_back_for_tall_video():
    assert caption_position(1080, 1920)[0] == 2


def test_ass_uses_real_pixels_and_escapes():
    ass = cues_to_ass([Cue(1.5, 3.25, "olha {isso}")], 1920, 1080)
    assert "PlayResY: 1920" in ass and "PlayResX: 1080" in ass
    assert "Dialogue: 0,0:00:01.50,0:00:03.25,Default,,0,0,0,,OLHA (ISSO)" in ass


def test_karaoke_highlights_each_spoken_word():
    cue = build_cues([w("mano", 0.0, 0.4), w("olha", 0.5, 0.9)])[0]
    ass = cues_to_ass([cue], 1920, 1080)
    lines = [ln for ln in ass.splitlines() if ln.startswith("Dialogue")]
    assert len(lines) == 2  # um evento por palavra
    assert "0:00:00.00,0:00:00.50" in lines[0] and "}MANO{" in lines[0] and lines[0].endswith(" OLHA")
    assert lines[1].startswith("Dialogue: 0,0:00:00.50") and "}OLHA{" in lines[1]
