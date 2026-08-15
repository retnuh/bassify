# Guitar Cancellation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace naive `L-R` bass extraction with a per-frequency complex-gain projection that cancels guitar bleed without touching bass, and build an objective before/after metric to measure it across the collection.

**Architecture:** `extract_bass()` gains a second, always-on output (`bass_clean.wav`) produced by a new numpy/scipy DSP stage (time-align → bass-free-frame detection → per-bin complex-gain projection → ffmpeg `asupercut` backstop). The existing dirty `bass.wav` path is untouched and keeps feeding `detect.py`. `combine.py` and `render/` switch their bass input from dirty to clean. A new `metrics.py` module + CLI command measures residual leak in bass-silent windows, reusing `detect.py`'s existing windows JSON.

**Tech Stack:** numpy, scipy.signal (STFT/ISTFT, cross-correlation), soundfile — all already transitively available via librosa (confirmed: scipy 1.18.0, numpy 2.5.2, soundfile 0.14.0 in the project venv). No new declared dependencies (matches `combine.py`'s existing precedent of importing numpy/soundfile without adding them to `pyproject.toml`).

**Global Constraints:**
- `bass.wav` (dirty) is never modified by this work — `detect.py` keeps consuming it unchanged.
- `bass_clean.wav` generation is always-on, no CLI flag.
- The clean-bass DSP stage is self-contained: it does its own bass-free-frame detection and must NOT depend on `detect.py`'s silence-windows JSON or reorder the pipeline (extract → detect → combine → remix → encode → render stays in that order).
- Fail-fast: insufficient bass-free calibration data for a track raises (`InsufficientCalibrationData`), caught and logged per-track by `extract_batch`/`run_batch` like any other failure — never a silent degraded output.
- `bass_clean.wav` must be frame-count-exact with `bass.wav` (dirty) for the same input+slice. Achieved by decoding the stereo source via ffmpeg first (same decoder/args as the dirty path), then running the numpy DSP stage on that decoded WAV — never load the (possibly-compressed) source directly via librosa for the clean path, to avoid ffmpeg-vs-librosa mp3-decode frame-count drift.
- Metric tool (`metrics.py`) reuses `detect.py`'s existing windows JSON directly — this is offline analysis tooling, not a pipeline stage, so no reorder concern applies to it.

**User decisions (already made):**
- Scope: handoff's steps 1-3 only (time-align, per-bin complex-gain projection, `asupercut` backstop). Steps 4-6 (spectral denoise, NLMS, Demucs) explicitly deferred, not built.
- Split: `detect.py` stays on dirty `bass.wav`; `combine.py` + render both switch to `bass_clean.wav`.
- Metric built first, before the DSP fix (already true by task order below — `metrics.py` in Task 8 is independent of the DSP tasks and could run first; regression test in Task 9 depends on both).
- `extract_bass()` gains the new code path directly (not a sibling module) — DSP functions live in `extract.py`.
- Bass-free frame detection is self-contained (own low-band energy gate), not reused from `detect.py`'s windows — but uses a **percentile-based** threshold specifically so count-in click transients (structurally bass-free, near track start) are naturally included without special-casing.
- Fallback on insufficient calibration data: **error out** (track-level fail-fast), not silent pass-through — user wants to see how bad it is across the collection.
- Projection applied to all bins uniformly, relying on regularization (no hard low-frequency floor/cutoff).
- Always-on, no `--clean` flag.
- Testing: both synthetic-signal unit tests AND real-track regression tests (tracks 06 "Dyna Flow", 40 "The Thrill Is Gone" — confirmed present at `tracks/BluesBass/06_Dyna Flow.mp3` and `tracks/BluesBass/40_The Thrill Is Gone.mp3`).

Full background and research: `docs/handoff-2026-08-15.md`. Design spec: `docs/superpowers/specs/2026-08-15-guitar-cancellation-design.md`.

---

## Task 1: Add `bass_clean` path to `paths.py`

**Goal:** `resolve_paths()` returns a `bass_clean` field alongside the existing `bass` field, following the same naming convention.

**Files:**
- Modify: `src/bassify/paths.py`
- Test: `tests/test_paths.py`

**Acceptance Criteria:**
- [ ] `Paths` dataclass has a `bass_clean: Path` field.
- [ ] `resolve_paths()` sets it to `{track}_bass_clean{sfx}.wav`, matching how `bass` is built.
- [ ] Slice suffix applies to `bass_clean` the same way it applies to `bass`.

**Verify:** `uv run pytest tests/test_paths.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_paths.py`:

```python
def test_default_layout_includes_bass_clean():
    p = resolve_paths(Path("tracks/BluesBass/01_The Twelve Bar Blues Form.mp3"))
    base = Path("out/BluesBass/01_The Twelve Bar Blues Form")
    assert p.bass_clean == base / "01_The Twelve Bar Blues Form_bass_clean.wav"


def test_slice_suffix_applied_to_bass_clean():
    p = resolve_paths(
        Path("tracks/BluesBass/01_x.mp3"), slice_spec=SliceSpec(duration=15, start=30)
    )
    assert p.bass_clean.name == "01_x_bass_clean_d15s_s30s.wav"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_paths.py -v`
Expected: FAIL — `AttributeError: 'Paths' object has no attribute 'bass_clean'`

- [ ] **Step 3: Implement**

In `src/bassify/paths.py`, add `bass_clean: Path` to the `Paths` dataclass (right after `bass`):

```python
@dataclass(frozen=True)
class Paths:
    track_dir: Path
    bass: Path
    bass_clean: Path
    windows: Path
    bass_only: Path
    remix: Path
    bass_only_m4a: Path
    remix_m4a: Path
    render_mp4: Path
    render_still_mp4: Path
    thumbnail_png: Path
    axis_png: Path
    wave_png: Path
    cover_jpg: Path
```

And in `resolve_paths()`, add the field to the returned `Paths(...)` call, right after `bass=name("bass", "wav"),`:

```python
        bass=name("bass", "wav"),
        bass_clean=name("bass_clean", "wav"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_paths.py -v`
Expected: PASS (all tests, including the two new ones)

- [ ] **Step 5: Commit**

```bash
git add src/bassify/paths.py tests/test_paths.py
git commit -m "feat(paths): add bass_clean artifact path"
```

---

## Task 2: Time-alignment functions (`estimate_delay`, `apply_fractional_delay`)

**Goal:** Pure numpy/scipy functions that estimate the sub-sample delay between two signals and apply a fractional-sample shift, forming the alignment step of the clean-bass DSP stage.

**Files:**
- Modify: `src/bassify/extract.py`
- Test: `tests/test_extract.py`

**Acceptance Criteria:**
- [ ] `estimate_delay(l, r, sr)` recovers a known integer-sample delay within 0.5 samples.
- [ ] `apply_fractional_delay` + `estimate_delay` round-trip: shifting a signal by a known fractional delay, then estimating and correcting it, re-aligns the signal (correlation > 0.99 in the interior).

**Verify:** `uv run pytest tests/test_extract.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_extract.py`:

```python
import numpy as np

from bassify.extract import apply_fractional_delay, estimate_delay


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_extract.py -v`
Expected: FAIL — `ImportError: cannot import name 'estimate_delay'`

- [ ] **Step 3: Implement**

In `src/bassify/extract.py`, add near the top (after existing imports):

```python
import numpy as np
from scipy.signal import correlate, correlation_lags
```

Add these functions (after `build_filter`, before `extract_bass`):

```python
def estimate_delay(l: np.ndarray, r: np.ndarray, sr: int, max_shift_seconds: float = 0.05) -> float:
    """Estimate the delay (in samples) by which ``r`` lags ``l``.

    Positive return value means r's content appears that many samples later
    than in l (r must be shifted earlier / advanced to align with l). Uses
    cross-correlation restricted to +/- max_shift_seconds around zero lag,
    with parabolic interpolation around the peak for sub-sample precision.
    """
    max_shift = max(1, int(max_shift_seconds * sr))
    n = min(len(l), len(r))
    a = l[:n]
    b = r[:n]

    corr = correlate(a, b, mode="full")
    lags = correlation_lags(len(a), len(b), mode="full")

    center = len(lags) // 2
    lo = max(0, center - max_shift)
    hi = min(len(lags), center + max_shift + 1)
    window = corr[lo:hi]
    window_lags = lags[lo:hi]

    peak_idx = int(np.argmax(window))
    peak_lag = float(window_lags[peak_idx])

    if 0 < peak_idx < len(window) - 1:
        y0, y1, y2 = window[peak_idx - 1], window[peak_idx], window[peak_idx + 1]
        denom = y0 - 2 * y1 + y2
        if denom != 0:
            peak_lag += 0.5 * (y0 - y2) / denom

    return peak_lag


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_extract.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/bassify/extract.py tests/test_extract.py
git commit -m "feat(extract): add sub-sample time-alignment functions"
```

---

## Task 3: Bass-free frame detection + per-bin projection gain fit

**Goal:** Pure functions that identify bass-free STFT frames (percentile-based, low-band energy) and fit a per-bin complex projection gain from them, raising `InsufficientCalibrationData` when there isn't enough bass-free content to trust the fit.

**Files:**
- Modify: `src/bassify/extract.py`
- Test: `tests/test_extract.py`

**Acceptance Criteria:**
- [ ] `bass_free_frame_mask` marks a genuinely quiet (low-band) region as bass-free more often than a loud region.
- [ ] `fit_projection_gains` recovers a known synthetic per-bin complex gain within tight tolerance.
- [ ] `fit_projection_gains` raises `InsufficientCalibrationData` when the bass-free mask has too few frames for the requested `min_seconds`.

**Verify:** `uv run pytest tests/test_extract.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_extract.py`:

```python
import json

import pytest
from scipy.signal import stft

from bassify.extract import (
    STFT_HOP,
    STFT_NOVERLAP,
    STFT_NPERSEG,
    InsufficientCalibrationData,
    bass_free_frame_mask,
    fit_projection_gains,
)


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_extract.py -v`
Expected: FAIL — `ImportError: cannot import name 'bass_free_frame_mask'`

- [ ] **Step 3: Implement**

In `src/bassify/extract.py`, add module-level constants (after `DEFAULT_LOWPASS`):

```python
STFT_NPERSEG = 2048
STFT_HOP = 512
STFT_NOVERLAP = STFT_NPERSEG - STFT_HOP  # 75% overlap

BASS_FREE_LOW_CUTOFF_HZ = 250.0
BASS_FREE_PERCENTILE = 30.0
MIN_BASS_FREE_SECONDS = 1.0
PROJECTION_EPS_REL = 1e-6


class InsufficientCalibrationData(RuntimeError):
    """Raised when a track has too little bass-free content to fit a reliable
    guitar-cancellation projection."""
```

Add these functions (after the alignment functions from Task 2):

```python
def bass_free_frame_mask(
    L_stft: np.ndarray,
    freqs: np.ndarray,
    low_cutoff: float = BASS_FREE_LOW_CUTOFF_HZ,
    percentile: float = BASS_FREE_PERCENTILE,
) -> np.ndarray:
    """Return a boolean mask (one per STFT frame) marking bass-free frames.

    A frame is bass-free when its low-band (<low_cutoff Hz) energy in L falls
    at or below the given percentile of the track's own low-band energy
    distribution. Percentile-based (not a fixed dB threshold) so brief
    broadband content -- notably count-in click transients, which are
    structurally bass-free -- is naturally included without special-casing,
    as long as it's a minority of the track's frame-time.
    """
    low_bins = freqs < low_cutoff
    low_energy = np.sum(np.abs(L_stft[low_bins, :]) ** 2, axis=0)
    threshold = np.percentile(low_energy, percentile)
    return low_energy <= threshold


def fit_projection_gains(
    L_stft: np.ndarray,
    R_stft: np.ndarray,
    mask: np.ndarray,
    sr: int,
    hop_length: int = STFT_HOP,
    min_seconds: float = MIN_BASS_FREE_SECONDS,
    eps_rel: float = PROJECTION_EPS_REL,
) -> np.ndarray:
    """Fit one complex gain per frequency bin from bass-free frames only.

    Ĥ[k] = sum(L[k,t]*conj(R[k,t])) / (sum(|R[k,t]|^2) + eps), over
    bass-free frames t. Raises InsufficientCalibrationData if fewer than
    min_seconds worth of bass-free frames are available -- fail fast rather
    than silently fitting on too little data.
    """
    n_bass_free = int(np.sum(mask))
    min_frames = max(1, int(min_seconds * sr / hop_length))
    if n_bass_free < min_frames:
        raise InsufficientCalibrationData(
            f"only {n_bass_free} bass-free frames found (need >= {min_frames} "
            f"for >= {min_seconds}s of calibration data)"
        )

    L_masked = L_stft[:, mask]
    R_masked = R_stft[:, mask]
    numerator = np.sum(L_masked * np.conj(R_masked), axis=1)
    denominator = np.sum(np.abs(R_masked) ** 2, axis=1)
    eps = eps_rel * np.mean(denominator)
    return numerator / (denominator + eps)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_extract.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/bassify/extract.py tests/test_extract.py
git commit -m "feat(extract): add bass-free frame detection and projection gain fit"
```

---

## Task 4: `project_clean_bass` orchestration

**Goal:** Combine alignment (Task 2) and projection fitting (Task 3) into one function that takes stereo L/R and returns a cleaned bass estimate, same length as the input.

**Files:**
- Modify: `src/bassify/extract.py`
- Test: `tests/test_extract.py`

**Acceptance Criteria:**
- [ ] Output length exactly matches input length.
- [ ] On a synthetic signal with a known mastering-gain mismatch between channels, residual leak in a bass-silent region drops to <10% of the naive-subtraction residual.
- [ ] Bass content in a bass-present region is preserved (correlation > 0.9 with the true bass signal).

**Verify:** `uv run pytest tests/test_extract.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing test**

Add to `tests/test_extract.py`:

```python
from bassify.extract import project_clean_bass


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_extract.py -v`
Expected: FAIL — `ImportError: cannot import name 'project_clean_bass'`

- [ ] **Step 3: Implement**

In `src/bassify/extract.py`, extend the scipy import added in Task 2 to also pull in `istft`/`stft`:

```python
from scipy.signal import correlate, correlation_lags, istft, stft
```

Add this function (after `fit_projection_gains`):

```python
def project_clean_bass(l: np.ndarray, r: np.ndarray, sr: int) -> np.ndarray:
    """Cancel non-bass leakage from l using r as reference, returning a bass
    estimate the same length as l.

    Steps: time-align r to l, STFT both, fit a per-bin complex projection
    gain from bass-free frames, subtract the projected reference, inverse-STFT.
    Raises InsufficientCalibrationData (propagated from fit_projection_gains)
    if the track doesn't have enough bass-free content to trust the fit.
    """
    n = len(l)
    delay = estimate_delay(l, r, sr)
    r_aligned = apply_fractional_delay(r, -delay)

    freqs, _, L_stft = stft(l, fs=sr, nperseg=STFT_NPERSEG, noverlap=STFT_NOVERLAP)
    _, _, R_stft = stft(r_aligned, fs=sr, nperseg=STFT_NPERSEG, noverlap=STFT_NOVERLAP)

    mask = bass_free_frame_mask(L_stft, freqs)
    h = fit_projection_gains(L_stft, R_stft, mask, sr=sr, hop_length=STFT_HOP)

    B_stft = L_stft - h[:, None] * R_stft
    _, bass = istft(B_stft, fs=sr, nperseg=STFT_NPERSEG, noverlap=STFT_NOVERLAP)

    if len(bass) > n:
        bass = bass[:n]
    elif len(bass) < n:
        bass = np.pad(bass, (0, n - len(bass)))
    return bass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_extract.py -v`
Expected: PASS (all tests). If the cancellation assertions fail, check the sign of the alignment correction (`apply_fractional_delay(r, -delay)`) first — a flipped sign is the most likely culprit.

- [ ] **Step 5: Commit**

```bash
git add src/bassify/extract.py tests/test_extract.py
git commit -m "feat(extract): add project_clean_bass DSP orchestration"
```

---

## Task 5: Wire `extract_bass()` to always produce `bass_clean.wav`

**Goal:** `extract_bass()` gains an `output_clean` parameter and always writes a clean bass WAV, decoding the source via ffmpeg first (matching the dirty path's decode exactly) so `bass_clean.wav` is frame-count-exact with `bass.wav`, then applying the `asupercut` backstop (with a portable fallback) via ffmpeg.

**Files:**
- Modify: `src/bassify/extract.py`
- Test: `tests/test_extract.py`, `tests/test_integration.py`

**Acceptance Criteria:**
- [ ] `extract_bass()` writes both `bass.wav` (unchanged) and `bass_clean.wav` (new).
- [ ] `bass_clean.wav` is mono, and its frame count exactly matches `bass.wav`'s frame count.
- [ ] `output_clean` defaults to `resolve_paths(...).bass_clean` the same way `output` defaults to `.bass`.
- [ ] `should_skip`/`force` semantics apply independently to the clean output (matches the dirty output's existing behavior).
- [ ] If the installed ffmpeg lacks `asupercut`, the fallback double-lowpass chain is used instead, and this is logged.

**Verify:** `uv run pytest tests/test_extract.py tests/test_integration.py -v` → all pass (integration tests skip if ffmpeg is missing)

**Steps:**

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_extract.py` (unit-level, mocking ffmpeg/DSP to test wiring logic without real audio):

```python
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
```

Add to `tests/test_integration.py` (real end-to-end, reusing the module's existing `_make_source` synthetic stereo fixture):

```python
@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
def test_extract_bass_produces_frame_exact_dirty_and_clean(tmp_path: Path) -> None:
    """bass_clean.wav is mono and frame-count-exact with bass.wav."""
    src = tmp_path / "Coll" / "track.wav"
    _make_source(src)

    dirty_out = tmp_path / "bass.wav"
    clean_out = tmp_path / "bass_clean.wav"
    extract_bass(src, output=dirty_out, output_clean=clean_out, cut_inputs=True)

    assert dirty_out.exists()
    assert clean_out.exists()
    with wave.open(str(clean_out), "rb") as w:
        assert w.getnchannels() == 1, f"expected mono, got {w.getnchannels()} channels"
    with wave.open(str(dirty_out), "rb") as wd, wave.open(str(clean_out), "rb") as wc:
        assert wc.getnframes() == wd.getnframes(), (
            f"frame count mismatch: dirty={wd.getnframes()} clean={wc.getnframes()}"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_extract.py tests/test_integration.py -v`
Expected: FAIL — `extract_bass()` doesn't accept `output_clean`, and `_extract_bass_clean`/`_extract_bass_clean` don't exist.

- [ ] **Step 3: Implement**

In `src/bassify/extract.py`, add these new imports:

```python
import tempfile

import soundfile as sf
```

And extend the existing `from bassify.ffmpeg import run_ffmpeg, should_skip` line to also pull in `FfmpegError`:

```python
from bassify.ffmpeg import FfmpegError, run_ffmpeg, should_skip
```

Add these constants (near the other constants from Task 3):

```python
ASUPERCUT_FILTER = f"asupercut=cutoff={DEFAULT_LOWPASS:g}:order=8"
ASUPERCUT_FALLBACK_FILTER = f"lowpass=f={DEFAULT_LOWPASS:g},lowpass=f={DEFAULT_LOWPASS:g}"
```

Replace the existing `extract_bass()` function entirely with:

```python
def extract_bass(
    input_path: Path,
    output: Path | None = None,
    output_clean: Path | None = None,
    lowpass: float | None = DEFAULT_LOWPASS,
    slice_spec: SliceSpec | None = None,
    cut_inputs: bool = True,
    force: bool = False,
) -> Path:
    """Isolate bass via L-R subtraction -> mono 24-bit WAV (dirty), and also
    write a per-bin-projection-cleaned mono 24-bit WAV (bass_clean.wav).

    Returns the dirty output path (unchanged return contract). The clean
    output is always written (no flag); its path is `output_clean` or, when
    not given, `resolve_paths(input_path, slice_spec=spec).bass_clean`.
    """
    spec = slice_spec or SliceSpec()
    resolved = resolve_paths(input_path, slice_spec=spec)
    out = output or resolved.bass
    out.parent.mkdir(parents=True, exist_ok=True)
    if should_skip(out, force):
        print(f"skip (exists): {out}")
    else:
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

    out_clean = output_clean or resolved.bass_clean
    out_clean.parent.mkdir(parents=True, exist_ok=True)
    if should_skip(out_clean, force):
        print(f"skip (exists): {out_clean}")
    else:
        _extract_bass_clean(input_path, out_clean, spec, cut_inputs)

    return out


def _extract_bass_clean(
    input_path: Path, out_clean: Path, spec: SliceSpec, cut_inputs: bool
) -> None:
    """Decode input via ffmpeg (same decoder/args as the dirty path, so the
    result is frame-exact with bass.wav), run the numpy projection DSP, then
    apply the asupercut backstop (with fallback) via ffmpeg.
    """
    with tempfile.TemporaryDirectory(dir=str(out_clean.parent)) as tmp_dir:
        tmp_stereo = Path(tmp_dir) / "stereo.wav"
        tmp_bass = Path(tmp_dir) / "bass_raw.wav"

        decode_args: list[str] = []
        if cut_inputs:
            decode_args += spec.input_args()
        decode_args += [
            "-i",
            str(input_path),
            "-vn",
            "-c:a",
            "pcm_s24le",
            str(tmp_stereo),
        ]
        run_ffmpeg(decode_args)

        l_r, sr = sf.read(str(tmp_stereo), dtype="float64", always_2d=True)
        l, r = l_r[:, 0], l_r[:, 1]

        bass = project_clean_bass(l, r, sr)
        sf.write(str(tmp_bass), bass, sr, subtype="PCM_24")

        try:
            run_ffmpeg(
                [
                    "-i",
                    str(tmp_bass),
                    "-af",
                    ASUPERCUT_FILTER,
                    "-vn",
                    "-c:a",
                    "pcm_s24le",
                    str(out_clean),
                ]
            )
        except FfmpegError:
            run_ffmpeg(
                [
                    "-i",
                    str(tmp_bass),
                    "-af",
                    ASUPERCUT_FALLBACK_FILTER,
                    "-vn",
                    "-c:a",
                    "pcm_s24le",
                    str(out_clean),
                ]
            )
            print(f"asupercut unavailable, used fallback lowpass chain for {out_clean}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_extract.py tests/test_integration.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run the full existing suite to check for regressions**

Run: `uv run pytest -q`
Expected: all tests pass (should be the prior 205 + new tests from Tasks 1-5, 0 failures)

- [ ] **Step 6: Commit**

```bash
git add src/bassify/extract.py tests/test_extract.py tests/test_integration.py
git commit -m "feat(extract): always produce frame-exact bass_clean.wav via per-bin projection"
```

---

## Task 6: Wire `pipeline.py` to use `bass_clean` for the audible mix

**Goal:** `run_pipeline()` passes `paths.bass_clean` explicitly to `extract_bass()` and switches `combine_track()`'s bass input from dirty `bass` to `bass_clean`. `detect_windows()` keeps using dirty `bass`, unchanged.

**Files:**
- Modify: `src/bassify/pipeline.py`
- Test: `tests/test_pipeline.py`

**Acceptance Criteria:**
- [ ] `extract_bass()` is called with `output_clean=paths.bass_clean`.
- [ ] `detect_windows()` still receives the dirty `bass` return value from `extract_bass()`, unchanged.
- [ ] `combine_track()` receives `paths.bass_clean`, not the dirty bass path, as its first argument.

**Verify:** `uv run pytest tests/test_pipeline.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline.py`:

```python
def test_run_pipeline_wires_bass_clean_to_combine_not_dirty_bass(monkeypatch, tmp_path):
    """extract_bass gets output_clean=paths.bass_clean; combine_track gets
    paths.bass_clean (not the dirty bass path) as its bass input."""
    input_mp3 = tmp_path / "tracks" / "Band" / "05_Song.mp3"
    input_mp3.parent.mkdir(parents=True)
    input_mp3.touch()

    paths = resolve_paths(input_mp3)
    calls = {}

    def fake_extract_bass(
        input_path,
        output=None,
        output_clean=None,
        lowpass=DEFAULT_LOWPASS,
        slice_spec=None,
        cut_inputs=True,
        force=False,
    ):
        calls["extract_output_clean"] = output_clean
        return paths.bass

    def fake_detect_windows(bass_path, **kwargs):
        calls["detect_bass_path"] = bass_path
        return paths.windows

    def fake_combine_track(bass_path, original_path, windows_path, **kwargs):
        calls["combine_bass_path"] = bass_path
        return paths.bass_only

    def fake_remix_track(combined_path, original_path, **kwargs):
        return paths.remix

    def fake_encode_track(wav_path, original_path, **kwargs):
        pass

    import bassify.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "extract_bass", fake_extract_bass)
    monkeypatch.setattr(pipeline_mod, "detect_windows", fake_detect_windows)
    monkeypatch.setattr(pipeline_mod, "combine_track", fake_combine_track)
    monkeypatch.setattr(pipeline_mod, "remix_track", fake_remix_track)
    monkeypatch.setattr(pipeline_mod, "encode_track", fake_encode_track)

    from bassify.pipeline import run_pipeline

    run_pipeline(input_mp3)

    assert calls["extract_output_clean"] == paths.bass_clean
    assert calls["detect_bass_path"] == paths.bass
    assert calls["combine_bass_path"] == paths.bass_clean
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL — `TypeError: fake_extract_bass() got an unexpected keyword argument` (the existing `run_pipeline` doesn't pass `output_clean` yet), or `combine_bass_path` assertion fails since it currently receives the dirty `bass` path.

- [ ] **Step 3: Implement**

In `src/bassify/pipeline.py`, in `run_pipeline()`, change:

```python
    bass = extract_bass(
        input_path,
        output=paths.bass,
        lowpass=lowpass,
        slice_spec=slice_spec,
        cut_inputs=True,
        force=force,
    )
    windows = detect_windows(
        bass,
        output=paths.windows,
        threshold=threshold,
        min_gap=min_gap,
        slice_spec=slice_spec,
        force=force,
        original_path=input_path,
    )
    bass_only = combine_track(
        bass,
        input_path,
        windows,
        output=paths.bass_only,
        slice_spec=slice_spec,
        force=force,
    )
```

to:

```python
    bass = extract_bass(
        input_path,
        output=paths.bass,
        output_clean=paths.bass_clean,
        lowpass=lowpass,
        slice_spec=slice_spec,
        cut_inputs=True,
        force=force,
    )
    windows = detect_windows(
        bass,
        output=paths.windows,
        threshold=threshold,
        min_gap=min_gap,
        slice_spec=slice_spec,
        force=force,
        original_path=input_path,
    )
    bass_only = combine_track(
        paths.bass_clean,
        input_path,
        windows,
        output=paths.bass_only,
        slice_spec=slice_spec,
        force=force,
    )
```

(`detect_windows` still receives `bass`, the dirty return value — unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: PASS (all tests, including the pre-existing `test_run_pipeline_call_order_and_slice_threading` — it doesn't assert on `output_clean` or the exact bass path passed to `combine_track`, so it should be unaffected; if it fails, check its `fake_extract_bass`/`fake_combine_track` signatures accept the new `output_clean` kwarg via `**kwargs` or an explicit parameter)

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/bassify/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): route combine_track to bass_clean instead of dirty bass"
```

---

## Task 7: Wire render to use `bass_clean` for CQT/waveform visuals

**Goal:** `resolve_render_inputs()` and `render_batch()` both look for `_bass_clean.wav` instead of `_bass.wav` alongside a `bass_only.m4a`.

**Files:**
- Modify: `src/bassify/render/__init__.py`
- Test: `tests/test_render_integration.py` (or wherever the existing render path-resolution tests live — search first)

**Acceptance Criteria:**
- [ ] `resolve_render_inputs()` looks for `{stem}_bass_clean.wav`, not `{stem}_bass.wav`.
- [ ] `render_batch()`'s co-located-file check also looks for `_bass_clean.wav`.
- [ ] Both call sites are updated consistently (no drift between them).

**Verify:** `uv run pytest tests/test_render_integration.py -v` → all pass (search for other render tests referencing `_bass.wav` first: `grep -rn "_bass\.wav\|resolve_render_inputs" tests/`)

**Steps:**

- [ ] **Step 1: Find existing tests covering this behavior**

Run: `grep -rn "resolve_render_inputs\|_bass\.wav" tests/`

Read whatever test(s) currently assert on `resolve_render_inputs()`'s filename construction or `render_batch()`'s co-located-file lookup, and update their expected filenames from `_bass.wav` to `_bass_clean.wav` in place (rather than adding new tests, since this is a rename of existing, already-tested behavior — check whether a test creates a sibling `..._bass.wav` fixture file and update it to create `..._bass_clean.wav` instead). If no existing test covers this, add one:

```python
def test_resolve_render_inputs_looks_for_bass_clean(tmp_path):
    from bassify.render import resolve_render_inputs

    bass_only = tmp_path / "01_Song_bass_only.m4a"
    bass_only.touch()
    bass_clean = tmp_path / "01_Song_bass_clean.wav"
    bass_clean.touch()

    assert resolve_render_inputs(bass_only) == bass_clean


def test_resolve_render_inputs_raises_without_bass_clean(tmp_path):
    from bassify.render import resolve_render_inputs

    bass_only = tmp_path / "01_Song_bass_only.m4a"
    bass_only.touch()
    # no _bass_clean.wav sibling created

    with pytest.raises(FileNotFoundError):
        resolve_render_inputs(bass_only)
```

- [ ] **Step 2: Run the located/added tests to verify they fail (or already reflect the old behavior)**

Run: `uv run pytest tests/test_render_integration.py -v` (adjust path per what Step 1 found)
Expected: FAIL against the new/updated assertions, since the code still looks for `_bass.wav`.

- [ ] **Step 3: Implement**

In `src/bassify/render/__init__.py`, change `resolve_render_inputs()`:

```python
def resolve_render_inputs(bass_only_m4a: Path) -> Path:
    """Return the co-located bass_clean.wav for a bass_only.m4a, or raise if missing."""
    bass_only_m4a = Path(bass_only_m4a)
    bass_wav = bass_only_m4a.with_name(
        bass_only_m4a.stem.replace("_bass_only", "_bass_clean") + ".wav"
    )
    if not bass_wav.exists():
        raise FileNotFoundError(
            f"No bass_clean.wav alongside {bass_only_m4a}. Run 'bassify run' first "
            f"(or 'bassify extract <dir>' to regenerate it). Expected: {bass_wav}"
        )
    return bass_wav
```

And change `render_batch()`'s lookup:

```python
def render_batch(directory: Path, **kwargs) -> None:
    """Render every *_bass_only*.m4a under directory that has a co-located bass_clean.wav."""
    directory = Path(directory)
    m4as = sorted(directory.rglob("*_bass_only*.m4a"))
    rendered = skipped = failed = 0
    for m in m4as:
        bass_wav = m.with_name(m.stem.replace("_bass_only", "_bass_clean") + ".wav")
        if not bass_wav.exists():
            skipped += 1
            continue
        try:
            render_track(m, **kwargs)
            rendered += 1
        except Exception as exc:  # noqa: BLE001
```

(leave the rest of `render_batch()`'s body — the `except` block and summary print — unchanged; only the `bass_wav` line and the docstring change.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_render_integration.py -v` (and any other test files touched in Step 1)
Expected: PASS (all tests)

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/bassify/render/__init__.py tests/
git commit -m "feat(render): resolve bass_clean.wav instead of dirty bass.wav for visuals"
```

---

## Task 8: `metrics.py` — residual-bleed measurement + `bassify measure-bleed` CLI command

**Goal:** A reusable function that scores residual (non-bass) energy during bass-silent windows, relative to the track's overall bass energy, plus a CLI command that runs it across a whole collection and prints a before/after table.

**Files:**
- Create: `src/bassify/metrics.py`
- Modify: `src/bassify/cli.py`
- Test: `tests/test_metrics.py`

**Acceptance Criteria:**
- [ ] `compute_residual_db(bass_path, windows_path)` returns a low (more negative) dB value when the bass-silent windows are quiet relative to the bass level, and a higher value when they leak loudly.
- [ ] `bassify measure-bleed <collection_dir>` prints a table comparing dirty vs clean residual dB per track directory under `out/<collection>/`.

**Verify:** `uv run pytest tests/test_metrics.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Create `tests/test_metrics.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from bassify.metrics import compute_residual_db


def test_compute_residual_db_lower_when_windows_quieter(tmp_path):
    sr = 8000
    n = sr * 4
    y = np.zeros(n)
    y[: n // 2] = 0.5  # bass-active region, loud
    y[n // 2 :] = 0.05  # bass-silent window, quiet residual

    bass_path = tmp_path / "bass.wav"
    sf.write(str(bass_path), y, sr, subtype="PCM_24")

    windows = [{"start": (n // 2) / sr, "end": n / sr}]
    windows_path = tmp_path / "windows.json"
    windows_path.write_text(json.dumps(windows))

    db = compute_residual_db(bass_path, windows_path)
    assert db < -10


def test_compute_residual_db_higher_when_windows_leak_loudly(tmp_path):
    sr = 8000
    n = sr * 4
    y = np.zeros(n)
    y[: n // 2] = 0.5
    y[n // 2 :] = 0.4  # loud leak, close to the bass level

    bass_path = tmp_path / "bass2.wav"
    sf.write(str(bass_path), y, sr, subtype="PCM_24")

    windows = [{"start": (n // 2) / sr, "end": n / sr}]
    windows_path = tmp_path / "windows2.json"
    windows_path.write_text(json.dumps(windows))

    db = compute_residual_db(bass_path, windows_path)
    assert db > -5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bassify.metrics'`

- [ ] **Step 3: Implement**

Create `src/bassify/metrics.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf


def compute_residual_db(bass_path: Path, windows_path: Path) -> float:
    """Residual (non-bass leak) energy during bass-silent windows, in dB
    relative to the track's overall bass energy outside those windows.

    Lower (more negative) is better -- less audible leak during gaps.
    """
    y, sr = sf.read(str(bass_path), dtype="float64", always_2d=False)
    windows = json.loads(Path(windows_path).read_text())

    silent_mask = np.zeros(len(y), dtype=bool)
    for w in windows:
        start_sample = int(w["start"] * sr)
        end_sample = min(int(w["end"] * sr), len(y))
        silent_mask[start_sample:end_sample] = True

    residual_rms = np.sqrt(np.mean(y[silent_mask] ** 2)) if silent_mask.any() else 0.0
    bass_rms = np.sqrt(np.mean(y[~silent_mask] ** 2)) if (~silent_mask).any() else 1e-12

    ratio = residual_rms / max(bass_rms, 1e-12)
    return float(20 * np.log10(max(ratio, 1e-12)))


def scan_collection(collection_dir: Path) -> list[tuple[str, float, float | None]]:
    """For each track directory under collection_dir, compute (name, before_db,
    after_db) using the dirty bass.wav and (if present) bass_clean.wav, both
    scored against the same silence-windows JSON.
    """
    rows: list[tuple[str, float, float | None]] = []
    for track_dir in sorted(Path(collection_dir).iterdir()):
        if not track_dir.is_dir():
            continue
        windows_path = next(track_dir.glob("*_silence_windows*.json"), None)
        bass_path = next(
            (p for p in track_dir.glob("*_bass.wav") if "_bass_clean" not in p.name), None
        )
        bass_clean_path = next(track_dir.glob("*_bass_clean.wav"), None)
        if windows_path is None or bass_path is None:
            continue
        before = compute_residual_db(bass_path, windows_path)
        after = compute_residual_db(bass_clean_path, windows_path) if bass_clean_path else None
        rows.append((track_dir.name, before, after))
    return rows


def print_report(rows: list[tuple[str, float, float | None]]) -> None:
    print(f"{'track':<45} {'before (dB)':>12} {'after (dB)':>12}")
    for name, before, after in rows:
        after_str = f"{after:.1f}" if after is not None else "n/a"
        print(f"{name:<45} {before:>12.1f} {after_str:>12}")
```

In `src/bassify/cli.py`, add this import near the top:

```python
from bassify import metrics as metrics_mod
```

Add this command (after the other `@app.command()` definitions):

```python
@app.command(name="measure-bleed")
def measure_bleed(collection_dir: Path) -> None:
    """Compare dirty vs clean residual guitar-bleed dB per track.

    Pass an out/<collection> directory (containing one subdirectory per
    track). Reuses each track's existing silence-windows JSON.
    """
    rows = metrics_mod.scan_collection(collection_dir)
    metrics_mod.print_report(rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_metrics.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Smoke-test the CLI command**

Run: `uv run bassify measure-bleed --help`
Expected: prints help text without error, showing the `collection_dir` argument.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add src/bassify/metrics.py src/bassify/cli.py tests/test_metrics.py
git commit -m "feat(metrics): add residual-bleed scoring and measure-bleed CLI command"
```

---

## Task 9: Real-track regression test on known-bad tracks (06, 40)

**Goal:** An integration test that runs the real extract+detect pipeline on tracks 06 ("Dyna Flow") and 40 ("The Thrill Is Gone") — both flagged in the handoff as having audible guitar bleed — and asserts the clean-bass residual score improves over the dirty-bass score by a meaningful margin, using `metrics.compute_residual_db`.

**Files:**
- Modify: `tests/test_integration.py`

**Acceptance Criteria:**
- [ ] Test is skipped (not failed) when `tracks/BluesBass/06_Dyna Flow.mp3` or `tracks/BluesBass/40_The Thrill Is Gone.mp3` aren't present locally (this directory is gitignored, user-local audio).
- [ ] Test is skipped when ffmpeg is missing (matches existing convention in this file).
- [ ] For each present track, `compute_residual_db(bass_clean, windows) < compute_residual_db(bass, windows) - 3.0` (at least 3 dB improvement).

**Verify:** `uv run pytest tests/test_integration.py -v -k known_bad` → passes if `tracks/BluesBass/` is present locally with the two named files, or reports "skipped" (not failed) otherwise.

**Steps:**

- [ ] **Step 1: Write the test**

Add to `tests/test_integration.py`:

```python
_REGRESSION_TRACKS_DIR = Path("tracks/BluesBass")
_REGRESSION_TRACKS = ["06_Dyna Flow.mp3", "40_The Thrill Is Gone.mp3"]
_missing_regression_tracks = [
    t for t in _REGRESSION_TRACKS if not (_REGRESSION_TRACKS_DIR / t).exists()
]


@pytest.mark.integration
@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
@pytest.mark.skipif(
    bool(_missing_regression_tracks),
    reason=f"real tracks not found locally: {_missing_regression_tracks}",
)
@pytest.mark.parametrize("track_name", _REGRESSION_TRACKS)
def test_clean_bass_improves_residual_on_known_bad_tracks(
    track_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression check on real known-bad tracks: clean-bass residual score
    must improve over dirty-bass by at least 3 dB."""
    from bassify.detect import detect_windows
    from bassify.metrics import compute_residual_db

    monkeypatch.chdir(tmp_path)
    src = (_REGRESSION_TRACKS_DIR / track_name).resolve()

    p = resolve_paths(src)
    extract_bass(src, force=True)
    windows = detect_windows(p.bass, original_path=src, force=True)

    before = compute_residual_db(p.bass, windows)
    after = compute_residual_db(p.bass_clean, windows)
    assert after < before - 3.0, f"{track_name}: before={before:.1f}dB after={after:.1f}dB"
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_integration.py -v -k known_bad`
Expected: on this machine (where `tracks/BluesBass/` is present, confirmed via `ls tracks/BluesBass/`), both parametrized cases run for real and must PASS. On a machine without `tracks/BluesBass/`, expect SKIPPED, not an error or failure — if it errors instead of skipping, the skip condition is wrong; fix it before proceeding.

If either real case fails (residual doesn't improve by 3dB), do NOT weaken the assertion — this is a genuine regression signal. Investigate via `uv run bassify measure-bleed out/BluesBass` (Task 8's CLI command) after running the full pipeline once on that track, and report the finding rather than silently loosening the threshold.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass (or skip, for the two new parametrized cases, on machines without `tracks/BluesBass/`)

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add real-track regression check for clean-bass residual improvement"
```

---

## Post-plan follow-up (not part of this plan)

- Run `uv run bassify measure-bleed out/BluesBass` across the full 43-track collection once Task 9 confirms the fix works on the two worst known tracks, to get objective before/after numbers for all of the handoff's flagged tracks (30, 14, 13, 25, 17, 18, 06, 10, 41, 09).
- Decide re-render scope (affected tracks only vs full collection) based on that scan.
- Track 09 needs a full re-render regardless (a slice command overwrote it in the prior session, per the handoff).
