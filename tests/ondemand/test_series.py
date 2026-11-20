from liveclips.ondemand.series import PART_MAX, PART_MIN, split_parts
from liveclips.ondemand.snap import sentence_ends, sentence_starts
from .test_peaks_snap import make_words


def _check(parts, words):
    starts, ends = set(sentence_starts(words)), set(sentence_ends(words))
    for a, b in parts:
        assert PART_MIN <= b - a <= PART_MAX
        assert a in starts and b in ends
    for (_, b1), (a2, _) in zip(parts, parts[1:]):
        assert b1 <= a2


def test_short_topic_is_a_single_part():
    words = make_words(n_sentences=40)
    assert split_parts(words, 0.0, 60.0) == [(0.0, 60.0)]


def test_long_topic_splits_in_two():
    words = make_words(n_sentences=60)
    s, e = 0.0, words[33 * 5 - 1]["end"]  # ~100 s
    parts = split_parts(words, s, e)
    assert parts is not None and len(parts) == 2
    assert parts[0][0] == s and parts[-1][1] == e
    _check(parts, words)


def test_very_long_topic_splits_in_three():
    words = make_words(n_sentences=60)
    e = words[50 * 5 - 1]["end"]  # ~150 s
    parts = split_parts(words, 0.0, e)
    assert parts is not None and len(parts) == 3
    _check(parts, words)


def test_no_sentence_break_returns_none():
    words = [{"word": "blablabla", "start": 0.0, "end": 100.0}]
    assert split_parts(words, 0.0, 100.0) is None
