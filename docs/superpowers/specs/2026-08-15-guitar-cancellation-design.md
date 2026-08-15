# Guitar Cancellation Design — 2026-08-15

## Problem

The bass extraction pipeline isolates bass via `L - R`, where L is the full
mix and R is the full mix minus bass. On some tracks (confirmed: 06, 07, 40;
full extent unknown — see metric section) guitar audibly bleeds into the
"bass only" output, and on the worst tracks (06) the leak is loud and
high-pitched.

Root cause: L and R are separately-mastered stems, so the non-bass content is
not identical between channels. Modeling the non-bass mix as `m`, R is
approximately `H·m` where `H(f)` is an unknown per-frequency transfer
function (level, phase, small delay, mastering EQ differences). Naive
`L - R` leaves a residual `m·(1 - H)` wherever `H ≠ 1` — that residual is the
leaked guitar. Raising the current `lowpass=f=800` filter's corner doesn't
help; it re-admits the guitar it's meant to remove. The fix belongs in the
cancellation math, not the filter.

Full background and research: `docs/handoff-2026-08-15.md`.

## Goals / non-goals

**Goals (this design):**
- Replace naive channel subtraction with a per-frequency complex-gain
  projection that cancels guitar without touching bass.
- Time-align R to L before subtraction (stems can be offset by a fraction of
  a sample).
- Swap the ffmpeg backstop filter for a steeper cutoff.
- Build an objective before/after metric so the fix is measurable across all
  43 tracks, not just the ~5 spot-checked so far.

**Non-goals (deferred, documented in the handoff, not built here):**
- Spectral-gate denoise pass (handoff step 4).
- NLMS adaptive filter (handoff step 5).
- Demucs-based rescue path (handoff step 6).
- Block-adaptive / time-varying `Ĥ(k,t)` (see "Approaches considered" — only
  build if the metric shows within-track drift).
- Deciding which tracks to re-render, or re-rendering. Separate follow-up
  once this fix lands (`bass.wav` — the dirty file — is unaffected by this
  change, so existing renders are not invalidated by *this* work; re-render
  only matters for tracks whose audible bass_only output changes).

## Constraint: don't break click/window detection

`bass.wav` (dirty `L - R` + gentle lowpass) is depended on by `detect.py` for
silence-gap and count-in "ghost click" detection — those clicks survive
*because* naive `L - R` leaves residual non-bass content in. A cleaner
cancellation would also remove the clicks `detect.py` needs. So the cleaner
bass is **not a drop-in replacement** for the detection input; it's an
additional, parallel output.

## Consumers of bass audio (traced from current code)

- `detect.py::detect_windows()` — takes `bass_path` directly (dirty), used
  for silence-window + count-in click detection. **Stays on dirty
  `bass.wav`.**
- `combine.py::combine_track()` — takes `bass_path` directly (dirty) for both
  the ffmpeg mix stream and the numpy donor-splice/ramp reconstruction (which
  re-reads the same bass file for consistency with what's in the mix).
  **Switches to clean `bass_clean.wav`.**
- `render/__init__.py::render_track()` — resolves a co-located `bass.wav`
  (dirty) from the `bass_only.m4a` filename, used to drive the CQT/waveform
  visuals. **Switches to clean `bass_clean.wav`.**

## Architecture

`extract_bass()` in `extract.py` gains a second, independent output. The
existing ffmpeg dirty path (`build_filter()` → `bass.wav`) is unchanged. A
new numpy/scipy stage runs alongside it and writes `bass_clean.wav`:

1. Load stereo L/R (soundfile/librosa, float64).
2. Detect bass-free frames: short-time RMS energy of a low-passed (<250 Hz)
   version of L, per STFT frame. Frames below a **percentile** threshold
   (e.g. bottom 30% of the low-band energy distribution for that track, not
   a fixed dB cutoff) are treated as bass-free calibration candidates.
   Percentile-based selection is chosen specifically so that brief broadband
   content — notably count-in click transients, which are structurally
   bass-free and appear near the start of most tracks — is naturally
   included without any dedicated count-in logic. This stage does not depend
   on `detect.py`'s silence windows; pipeline order is unchanged
   (extract → detect → combine → remix → encode → render).
3. Time-align R to L: integer-sample lag via `scipy.signal.correlate`,
   sub-sample refinement via parabolic interpolation around the peak, apply
   a fractional delay to R. L is never shifted.
4. STFT both channels (`scipy.signal.stft`, ~2048-sample window, 75%
   overlap). Fit one complex gain `Ĥ[k]` per frequency bin, using only the
   bass-free frames identified in step 2:
   `Ĥ[k] = Σ(L[k,t]·conj(R[k,t])) / (Σ|R[k,t]|² + ε)`
   over bass-free frames `t`.
5. **Fail-fast guardrail:** if the track doesn't have enough bass-free
   calibration frames overall to trust the fit (below a minimum
   count/duration threshold), raise an error for that track rather than
   silently emitting a degraded `bass_clean.wav`. `extract_batch`/`run_batch`
   already catch and log per-track failures and continue — this fits that
   existing pattern. No per-bin partial-fit fallback; the check is
   track-level.
6. Reconstruct: `b̂[k,t] = L[k,t] - Ĥ[k]·R[k,t]`, inverse STFT
   (`scipy.signal.istft`) with matching window/overlap.
7. Write the numpy-cleaned intermediate, hand off to ffmpeg for the backstop
   filter and encode to `bass_clean.wav`: `asupercut=cutoff=800:order=8`,
   falling back to a chained `lowpass=f=800,lowpass=f=800` (~-24 dB/oct) if
   the installed ffmpeg build lacks `asupercut` (detect via trying the
   filter and catching the ffmpeg error; log which path was used).

This is always-on — no CLI flag. Every `extract_bass()` call produces both
`bass.wav` and `bass_clean.wav`: `output_clean` defaults to
`resolve_paths(...).bass_clean` the same way `output` already defaults to
`.bass`, so `extract_batch()` (used standalone to regenerate WAVs after
`just clean`) gets clean bass for free with no call-site change.

### Paths

`paths.py`'s `Paths` dataclass gains `bass_clean: Path`
(`{track}_bass_clean{sfx}.wav`), alongside the existing `bass` field.

### Pipeline wiring

`pipeline.py::run_pipeline()`:
- `extract_bass()` gains an `output_clean: Path | None` parameter (mirrors
  the existing `output` param), writes `bass_clean.wav` there. Return type is
  unchanged (still returns the dirty `bass` path) — `pipeline.py` already
  has `paths.bass_clean` from `resolve_paths()` and passes it explicitly, so
  no caller needs the clean path threaded back through a return value.
- `detect_windows(bass, ...)` — unchanged, dirty input.
- `combine_track(bass_clean, ...)` — switched from `bass` to `bass_clean`.
- Render's `resolve_render_inputs()` **and** `render_batch()` — both
  independently reconstruct the co-located bass filename from the
  `bass_only.m4a` stem (`_bass_only` → `_bass`). Both call sites switch to
  `_bass_clean` so neither drifts from the other.

## Approaches considered

**A. Global per-bin complex gain (chosen).** One alignment pass, one STFT
over the whole track, one `Ĥ[k]` per bin fit once from bass-free frames,
applied uniformly. Simple, one estimation pass per track, straightforward to
unit-test with synthetic fixtures.

**B. Block-adaptive time-varying gain (deferred).** Re-fit `Ĥ[k]`
periodically with smoothing, to track mastering-chain drift within a track.
Not building this now — no evidence yet that within-track drift is a real
problem for these (studio, presumably static-master) stems. The metric will
surface whether residual guitar correlates with track position; if it does,
this is the next escalation.

**C. NLMS adaptive filter (deferred).** Handoff step 5, explicitly out of
scope for this cut.

Chosen: A, per YAGNI — build the simple version, let the metric tell us if
it's insufficient.

## The metric (built first, before the DSP change)

Standalone tool, not part of the production pipeline (e.g.
`scripts/measure_bleed.py`). For each of the 43 source tracks:

1. Read the existing silence-windows JSON (`detect.py` output) for
   bass-silent spans.
2. Within those spans, compute residual energy of the bass file under test,
   relative to that track's overall bass energy, in dB.
3. Emit a table: dirty-bass score vs clean-bass score per track, with the
   handoff's known worst-offenders (30, 14, 13, 25, 17, 18, 06, 10, 41, 09)
   highlighted for a targeted before/after check.

This tool is built and run against the *existing* dirty `bass.wav` first (to
get a real baseline across all 43, not just the handful spot-checked so
far), then re-run once `bass_clean.wav` exists per track.

## Testing

- **Synthetic fixtures**: construct a fake L (bass + synthetic "guitar") and
  R (`H(f)·guitar` with a known sub-sample delay injected). Assert:
  - alignment recovers the injected delay within tolerance,
  - the projection recovers the injected bass and cancels the injected
    guitar within a numeric tolerance,
  - a fixture with too little bass-free content raises the fail-fast error.
- **Real-track regression**: run the metric tool against 1-2 known-bad real
  tracks (06, 40); assert the clean-bass score improves over the dirty-bass
  score by a margin.
- Existing test suite (205 tests) stays green. New tests live alongside
  `extract.py`'s existing test file, following current repo conventions.

## Error handling

- Insufficient bass-free calibration data for a track → raise, caught and
  logged by `extract_batch`/`run_batch` like any other per-track failure
  (matches existing behavior — see the `except Exception` blocks in
  `pipeline.py`).
- `asupercut` unavailable in the installed ffmpeg build → fall back to
  chained `lowpass=f=800,lowpass=f=800`, log which path was taken.

## Open follow-up (not part of this design)

- Deciding scope of re-render (affected tracks only vs full collection) once
  this fix lands and changes `bass_only`/`remix` audio. Track 09 separately
  needs a full re-render regardless (a slice command overwrote it during the
  prior session).
