from __future__ import annotations

import numpy as np

from bassify.extract import apply_fractional_delay, build_filter, estimate_delay


def test_build_filter_no_lowpass():
    assert build_filter(None) == "pan=mono|c0=c0-c1"


def test_build_filter_800hz():
    assert build_filter(800) == "pan=mono|c0=c0-c1,lowpass=f=800"


def test_build_filter_500_renders_without_decimal():
    assert build_filter(500.0) == "pan=mono|c0=c0-c1,lowpass=f=500"


def test_estimate_delay_recovers_known_integer_shift():
    sr = 8000
    rng = np.random.default_rng(0)
    n = sr * 2
    l = rng.standard_normal(n)
    shift = 37  # samples; r lags l by this many samples
    r = np.zeros(n)
    r[shift:] = l[: n - shift]

    delay = estimate_delay(l, r, sr)

    assert abs(delay - shift) < 0.5


def test_align_round_trip_recovers_fractional_delay():
    sr = 8000
    rng = np.random.default_rng(1)
    n = sr * 2
    l = rng.standard_normal(n)
    true_delay = 12.7  # fractional samples; r lags l by this much
    r = apply_fractional_delay(l, true_delay)

    estimated = estimate_delay(l, r, sr)
    assert abs(estimated - true_delay) < 0.1

    corrected = apply_fractional_delay(r, -estimated)
    edge = 50  # ignore edges: the shift zero-pads them
    corr_coef = np.corrcoef(l[edge:-edge], corrected[edge:-edge])[0, 1]
    assert corr_coef > 0.99
