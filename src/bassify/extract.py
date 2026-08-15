from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.signal import correlate, correlation_lags

from bassify.ffmpeg import run_ffmpeg, should_skip
from bassify.paths import resolve_paths
from bassify.slice import SliceSpec

DEFAULT_LOWPASS = 800.0


def build_filter(lowpass: float | None) -> str:
    f = "pan=mono|c0=c0-c1"
    if lowpass is not None:
        f += f",lowpass=f={lowpass:g}"
    return f


def estimate_delay(l: np.ndarray, r: np.ndarray, sr: int, max_shift_seconds: float = 0.05) -> float:
    """Estimate the delay (in samples) by which ``r`` lags ``l``.

    Positive return value means r's content appears that many samples later
    than in l (r must be shifted earlier / advanced to align with l). Uses
    cross-correlation restricted to +/- max_shift_seconds around zero lag to
    find the integer-sample peak, then refines to sub-sample precision by
    maximizing the actual alignment score (via apply_fractional_delay) in a
    +/-1 sample neighborhood of that peak.

    Note: a 3-point parabolic fit around the integer peak was tried first,
    but the cross-correlation of broadband/noise-like signals is sinc-shaped
    near its peak (the impulse response of the ideal fractional-delay
    filter), not parabolic -- the parabolic fit was measurably biased
    (~0.1 sample error on synthetic-noise test fixtures). Directly
    maximizing alignment score sidesteps that shape assumption entirely.
    """
    max_shift = max(1, int(max_shift_seconds * sr))
    n = min(len(l), len(r))
    a = l[:n]
    b = r[:n]

    corr = correlate(b, a, mode="full")
    lags = correlation_lags(len(b), len(a), mode="full")

    center = len(lags) // 2
    lo = max(0, center - max_shift)
    hi = min(len(lags), center + max_shift + 1)
    window = corr[lo:hi]
    window_lags = lags[lo:hi]

    peak_idx = int(np.argmax(window))
    coarse_lag = float(window_lags[peak_idx])

    def neg_alignment(delay: float) -> float:
        aligned = apply_fractional_delay(b, -delay)
        return -float(np.dot(a, aligned))

    result = minimize_scalar(
        neg_alignment,
        bounds=(coarse_lag - 1.0, coarse_lag + 1.0),
        method="bounded",
        options={"xatol": 1e-4},
    )
    return float(result.x)


def apply_fractional_delay(x: np.ndarray, delay_samples: float) -> np.ndarray:
    """Shift ``x`` by ``delay_samples`` using an FFT-based fractional shift.

    Positive delay_samples shifts x LATER (toward higher indices). Output is
    the same length as x; content shifted past an edge is dropped and the
    vacated edge is implicitly zero-filled by the FFT round-trip.
    """
    n = len(x)
    if n == 0:
        return x.copy()
    spectrum = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n)
    phase_shift = np.exp(-2j * np.pi * freqs * delay_samples)
    return np.fft.irfft(spectrum * phase_shift, n=n)


def extract_bass(
    input_path: Path,
    output: Path | None = None,
    lowpass: float | None = DEFAULT_LOWPASS,
    slice_spec: SliceSpec | None = None,
    cut_inputs: bool = True,
    force: bool = False,
) -> Path:
    """Isolate bass via L-R subtraction -> mono 24-bit WAV. Returns output path."""
    spec = slice_spec or SliceSpec()
    out = output or resolve_paths(input_path, slice_spec=spec).bass
    out.parent.mkdir(parents=True, exist_ok=True)
    if should_skip(out, force):
        print(f"skip (exists): {out}")
        return out
    args: list[str] = []
    if cut_inputs:
        args += spec.input_args()
    args += [
        "-i",
        str(input_path),
        "-af",
        build_filter(lowpass),
        "-vn",
        "-c:a",
        "pcm_s24le",
        str(out),
    ]
    run_ffmpeg(args)
    return out
