import math

from liveclips.capture.spike import SpikeDetector, spike_score


def feed(det, start, count, text="oi", window=10.0):
    """Distribui `count` mensagens dentro da janela [start, start+window)."""
    for k in range(count):
        det.add_message(start + k * (window / max(count, 1)) * 0.99, text)


def warm(det, windows=7, per_window=2):
    for w in range(windows):
        feed(det, w * 10.0, per_window)


def test_no_spike_during_warmup():
    det = SpikeDetector()
    feed(det, 0.0, 2)
    feed(det, 10.0, 60, "kkkkk")  # rajada na 2ª janela: ainda em aquecimento
    assert det.poll(20.0) is None


def test_spike_after_warmup_with_keywords():
    det = SpikeDetector()
    warm(det)  # janelas 0..6 (t=0..70) com 2 msgs
    feed(det, 70.0, 30, "KKKKKKK clipa")
    spike = det.poll(80.0)
    assert spike is not None
    assert 14 <= spike.ratio <= 16
    assert spike.keyword_bonus == 1.0
    assert spike.msgs == 30


def test_below_min_msgs_does_not_fire():
    det = SpikeDetector(min_msgs=15)
    warm(det, per_window=1)
    feed(det, 70.0, 10, "kkkk")
    assert det.poll(80.0) is None


def test_cooldown_blocks_then_releases():
    det = SpikeDetector(cooldown_s=180)
    warm(det)
    feed(det, 70.0, 30)
    assert det.poll(80.0) is not None
    feed(det, 90.0, 40)
    assert det.poll(100.0) is None  # dentro do cooldown
    for w in range(10, 26):  # silêncio moderado até t=260
        feed(det, w * 10.0, 2)
    feed(det, 260.0, 60)
    assert det.poll(270.0) is not None


def test_poll_closes_empty_windows_after_silence():
    det = SpikeDetector()
    warm(det)
    assert det.poll(10_000.0) is None
    assert det.baseline is not None and det.baseline < 2


def test_spike_score_formula():
    assert spike_score(10.0, 0.5, 1000) == 10.0 * 1.5 * 3.0
    assert spike_score(4.0, 0.0, 1) == 4.0 * math.log10(10)
