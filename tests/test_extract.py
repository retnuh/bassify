from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import stft

from bassify.extract import (
    STFT_HOP,
    STFT_NOVERLAP,
    STFT_NPERSEG,
    InsufficientCalibrationData,
    apply_fractional_delay,
    bass_free_frame_mask,
    build_filter,
    estimate_delay,
    fit_projection_gains,
    project_clean_bass,
)


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


def test_bass_free_frame_mask_flags_quiet_frames_more_often():
    sr = 8000
    n = sr * 4
    t = np.arange(n) / sr
    low = np.sin(2 * np.pi * 100 * t)
    low[n // 2 :] = 0.0  # second half is low-band silent

    freqs, _, L = stft(low, fs=sr, nperseg=STFT_NPERSEG, noverlap=STFT_NOVERLAP)
    mask = bass_free_frame_mask(L, freqs)

    n_frames = mask.shape[0]
    first_half_rate = mask[: n_frames // 2].mean()
    second_half_rate = mask[n_frames // 2 :].mean()
    assert second_half_rate > first_half_rate


def test_fit_projection_gains_recovers_known_gain():
    rng = np.random.default_rng(2)
    n_bins, n_frames = 10, 200
    R = rng.standard_normal((n_bins, n_frames)) + 1j * rng.standard_normal((n_bins, n_frames))
    true_h = np.full(n_bins, 0.5 + 0.1j)
    L = true_h[:, None] * R
    mask = np.ones(n_frames, dtype=bool)

    h = fit_projection_gains(L, R, mask, sr=8000, hop_length=STFT_HOP, min_seconds=0.0)

    assert np.allclose(h, true_h, atol=1e-6)


def test_fit_projection_gains_raises_on_insufficient_data():
    rng = np.random.default_rng(3)
    n_bins, n_frames = 10, 5
    R = rng.standard_normal((n_bins, n_frames)) + 1j * rng.standard_normal((n_bins, n_frames))
    L = R.copy()
    mask = np.array([True, False, False, False, False])

    with pytest.raises(InsufficientCalibrationData):
        fit_projection_gains(L, R, mask, sr=8000, hop_length=STFT_HOP, min_seconds=1.0)


def test_project_clean_bass_cancels_reference_leakage_and_preserves_length():
    sr = 8000
    rng = np.random.default_rng(4)
    n = sr * 4
    bass = rng.standard_normal(n) * 0.3
    bass[n // 2 :] = 0.0  # bass-free calibration region in the second half
    guitar = rng.standard_normal(n)
    true_h = 0.7  # flat mastering-gain mismatch for this test
    l = bass + guitar
    r = true_h * guitar

    b_hat = project_clean_bass(l, r, sr)

    assert len(b_hat) == n

    residual_after = np.std(b_hat[n // 2 :])
    residual_before = np.std(l[n // 2 :])
    assert residual_after < 0.1 * residual_before

    corr = np.corrcoef(bass[: n // 2], b_hat[: n // 2])[0, 1]
    assert corr > 0.9
