import numpy as np

from liveclips.ondemand.peaks import pick_peaks
from liveclips.ondemand.snap import (MAX_LEN, ends_sentence, extend_past_reaction, sentences,
                                     snap_clip)


def make_words(n_sentences=40, words_per=5, gap=0.1, word_len=0.5):
    """Frases de 5 palavras, a última termina com ponto; cada frase dura ~3 s."""
    words, t = [], 0.0
    for s in range(n_sentences):
        for i in range(words_per):
            w = f"p{s}_{i}" + ("." if i == words_per - 1 else "")
            words.append({"word": w, "start": round(t, 2), "end": round(t + word_len, 2)})
            t += word_len + gap
    return words


def test_pick_peaks_respects_separation():
    s = np.zeros(1000)
    s[100], s[150], s[500] = 1.0, 0.9, 0.8
    assert pick_peaks(s, max_peaks=5, min_separation=90, smooth=1) == [100, 500]


def test_ends_sentence_ignores_ellipsis():
    assert ends_sentence("gente.") and ends_sentence("sério?")
    assert not ends_sentence("então...") and not ends_sentence("mas")


def test_snap_lands_on_sentence_edges_without_cutting_words():
    words = make_words()
    clip = snap_clip(words, peak=40.0, start=31.3, end=55.2)
    assert clip is not None
    s, e = clip
    starts = {w["start"] for w in words}
    ends = {w["end"] for w in words}
    assert s in starts and e in ends  # nunca no meio de uma palavra
    first = next(w for w in words if w["start"] == s)
    prev = words[words.index(first) - 1]
    assert ends_sentence(prev["word"])  # começa logo depois de um ponto
    last = next(w for w in words if w["end"] == e)
    assert ends_sentence(last["word"])
    assert s <= 40.0 - 6 and e >= 40.0 + 3


def test_snap_caps_length_around_the_peak():
    words = make_words(n_sentences=60)
    s, e = snap_clip(words, peak=90.0, start=10.0, end=170.0)
    assert e - s <= MAX_LEN and s <= 84.0 and e >= 93.0


def test_snap_returns_none_when_too_short_or_no_words():
    assert snap_clip([], 10.0, 0.0, 20.0) is None
    assert snap_clip(make_words(n_sentences=3), peak=5.0, start=0.0, end=9.0) is None


def test_reaction_extends_end_until_volume_drops():
    words = [{"word": "olha.", "start": 0.0, "end": 10.0},
             {"word": "depois", "start": 30.0, "end": 30.5}]
    rms = np.full(60, -30.0)
    rms[10:14] = -15.0  # risada de 4 s depois da fala
    assert extend_past_reaction(10.0, words, rms) == 14.0
    assert extend_past_reaction(10.0, words, None) == 10.0


def test_sentences_group_words():
    sents = sentences(make_words(n_sentences=2))
    assert len(sents) == 2 and sents[0][2].endswith(".")


def test_reaction_never_enters_contiguous_next_word():
    words = [{"word": "olha.", "start": 0.0, "end": 10.0},
             {"word": "entao", "start": 10.0, "end": 10.6}]
    rms = np.full(60, -30.0)
    rms[10:20] = -18.0
    assert extend_past_reaction(10.0, words, rms) == 10.0


def test_reaction_does_not_stretch_end_without_laughter():
    words = [{"word": "olha.", "start": 0.0, "end": 10.4},
             {"word": "depois", "start": 30.0, "end": 30.5}]
    rms = np.full(60, -30.0)
    rms[10] = -15.0  # ainda é a fala que acabou de terminar
    assert extend_past_reaction(10.4, words, rms) == 10.4
