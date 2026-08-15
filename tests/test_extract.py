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
    L = rng.standard_normal(n)
    shift = 37  # samples; r lags L by this many samples
    r = np.zeros(n)
    r[shift:] = L[: n - shift]

    delay = estimate_delay(L, r, sr)

    assert abs(delay - shift) < 0.5


def test_align_round_trip_recovers_fractional_delay():
    sr = 8000
    rng = np.random.default_rng(1)
    n = sr * 2
    L = rng.standard_normal(n)
    true_delay = 12.7  # fractional samples; r lags L by this much
    r = apply_fractional_delay(L, true_delay)

    estimated = estimate_delay(L, r, sr)
    assert abs(estimated - true_delay) < 0.1

    corrected = apply_fractional_delay(r, -estimated)
    edge = 50  # ignore edges: the shift wraps circularly there, not zero-fill
    corr_coef = np.corrcoef(L[edge:-edge], corrected[edge:-edge])[0, 1]
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
    # Fractional samples; r's guitar content lags l's by this much. Chosen large
    # relative to STFT_HOP (512) so a wrong-sign alignment correction produces a
    # net frame-index misalignment that the per-bin projection gain cannot
    # absorb (small residual delays, well under one hop, are just a linear
    # phase per frequency bin and get silently corrected by the projection fit
    # regardless of alignment sign -- see task-4-report.md for the empirical
    # sweep that found this threshold).
    true_delay = 250.7
    L = bass + guitar
    r = true_h * apply_fractional_delay(guitar, true_delay)

    b_hat = project_clean_bass(L, r, sr)

    assert len(b_hat) == n

    naive_residual = L - r  # naive-subtraction residual, unaligned and ungained
    residual_after = np.std(b_hat[n // 2 :])
    residual_before = np.std(naive_residual[n // 2 :])
    assert residual_after < 0.1 * residual_before

    corr = np.corrcoef(bass[: n // 2], b_hat[: n // 2])[0, 1]
    assert corr > 0.9


def test_extract_bass_output_clean_defaults_from_resolve_paths(tmp_path, monkeypatch):
    from bassify import extract as extract_mod

    input_mp3 = tmp_path / "tracks" / "Band" / "01_Song.mp3"
    input_mp3.parent.mkdir(parents=True)
    input_mp3.touch()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(extract_mod, "run_ffmpeg", lambda args: None)

    calls = []

    def fake_extract_bass_clean(input_path, out_clean, spec, cut_inputs):
        calls.append(out_clean)
        out_clean.parent.mkdir(parents=True, exist_ok=True)
        out_clean.touch()

    monkeypatch.setattr(extract_mod, "_extract_bass_clean", fake_extract_bass_clean)

    from bassify.paths import resolve_paths

    out = extract_mod.extract_bass(input_mp3)
    expected_clean = resolve_paths(input_mp3).bass_clean

    assert out == resolve_paths(input_mp3).bass
    assert calls == [expected_clean]


def test_backstop_filter_is_a_six_stage_lowpass_chain():
    """The backstop must be six chained 2-pole lowpass stages (12 poles,
    ~-72 dB/oct) at DEFAULT_LOWPASS.

    The previous constant was `asupercut=cutoff=800:order=8`, which never
    ran: asupercut's cutoff range is 20000-192000 Hz, so every track failed
    the filter on a parameter-range error and silently took a 4-pole
    fallback. Pinning the exact chain here is what keeps that from
    recurring.
    """
    from bassify import extract as extract_mod

    assert extract_mod.BACKSTOP_STAGES == 6
    stages = extract_mod.BACKSTOP_FILTER.split(",")
    assert len(stages) == 6
    assert all(s == f"lowpass=f={extract_mod.DEFAULT_LOWPASS:g}" for s in stages)
    assert not hasattr(extract_mod, "ASUPERCUT_FILTER")
    assert not hasattr(extract_mod, "ASUPERCUT_FALLBACK_FILTER")


def test_extract_bass_clean_makes_one_backstop_call_and_does_not_swallow_errors(
    tmp_path, monkeypatch
):
    """One ffmpeg backstop call, using BACKSTOP_FILTER, with no fallback.

    An FfmpegError from the backstop must propagate rather than being
    caught and silently downgraded to a weaker filter -- the silent
    downgrade is exactly how the dead asupercut path shipped unnoticed.
    """
    from bassify import extract as extract_mod
    from bassify.ffmpeg import FfmpegError
    from bassify.slice import SliceSpec

    ffmpeg_calls: list[list[str]] = []

    def fake_run_ffmpeg(args):
        ffmpeg_calls.append(list(args))
        if extract_mod.BACKSTOP_FILTER in args:
            raise FfmpegError("backstop failed")

    monkeypatch.setattr(extract_mod, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(extract_mod.sf, "read", lambda *a, **k: (np.zeros((100, 2)), 8000))
    monkeypatch.setattr(extract_mod.sf, "write", lambda *a, **k: None)
    monkeypatch.setattr(extract_mod, "project_clean_bass", lambda left, r, sr: np.zeros(100))

    with pytest.raises(FfmpegError):
        extract_mod._extract_bass_clean(
            tmp_path / "input.wav", tmp_path / "bass_clean.wav", SliceSpec(), True
        )

    backstop_calls = [c for c in ffmpeg_calls if extract_mod.BACKSTOP_FILTER in c]
    assert len(backstop_calls) == 1, "expected exactly one backstop call, no fallback"
