import numpy as np

from liveclips.ondemand.signals import (chat_signal, combine, heatmap_signal, loudness_signal,
                                        moving_average, robust_norm)


def test_robust_norm_is_between_0_and_1():
    x = robust_norm(np.array([0, 1, 2, 3, 100], dtype=float))
    assert x.min() == 0 and x.max() == 1


def test_moving_average_keeps_length():
    assert moving_average(np.ones(10), 5).shape == (10,)


def test_loudness_jump_stands_out():
    db = np.full(300, -30.0)
    db[150:153] = -10.0  # risada
    s = loudness_signal(db)
    assert s.argmax() in (150, 151, 152) and s[50] < 0.2


def test_heatmap_interpolates_and_skips_when_missing():
    hm = [{"start_time": 0, "end_time": 10, "value": 0.1},
          {"start_time": 10, "end_time": 20, "value": 1.0},
          {"start_time": 20, "end_time": 30, "value": 0.1}]
    s = heatmap_signal(hm, 30)
    assert s.shape == (30,) and 10 <= s.argmax() <= 19
    assert heatmap_signal(None, 30) is None and heatmap_signal(hm[:1], 30) is None


def test_chat_counts_reactions_per_second():
    s = chat_signal([100.0] * 30 + [5.0], 200)
    assert 98 <= s.argmax() <= 102
    assert chat_signal([], 200) is None


def test_combine_renormalizes_weights_and_rewards_agreement():
    n = 10
    a, b = np.zeros(n), np.zeros(n)
    a[3], b[3] = 1.0, 1.0
    a[7] = 1.0
    out = combine({"heatmap": a, "loudness": b, "chat": None})
    assert abs(out[3] - (1.0 + 0.15)) < 1e-9  # os dois concordam
    assert abs(out[7] - 0.45 / 0.65) < 1e-9  # só o heatmap, peso renormalizado
    assert combine({"heatmap": None}).size == 0


def test_moving_average_and_signals_keep_length_for_short_inputs():
    assert moving_average(np.ones(10), 61).shape == (10,)
    assert loudness_signal(np.full(10, -30.0)).shape == (10,)
    assert chat_signal([0.0, 1.0], 3).shape == (3,)
