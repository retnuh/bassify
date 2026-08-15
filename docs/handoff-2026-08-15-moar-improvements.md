# Guitar Cancellation — Handoff / Brainstorm Input — 2026-08-16

This document hands off the completed guitar-cancellation feature and, more
importantly, the findings from testing it against the real 43-track
collection — which surfaced two follow-on problems worth a proper
brainstorm before more DSP work happens.

## TL;DR

- **Guitar-cancellation feature (plan Tasks 1-9) is complete and merged** on
  `worktree-guitar-cancellation`, all 224 tests passing. Per-bin complex-gain
  projection replaces naive `L-R` subtraction; `bass_clean.wav` is now
  always produced alongside the existing dirty `bass.wav`; `combine.py` and
  `render/` both consume the clean version.
- **The measure-bleed metric was redesigned twice tonight**, live, against
  real audio — the original design (spec'd during brainstorming, built in
  Task 8) measured the wrong thing. See "The metric's wrong turns" below.
- **Collection-wide result, correctly measured: no regressions, real
  improvement everywhere.** High-band (guitar-range) energy drops -3.8dB to
  -26.5dB across all 43 tracks during active music. Bass itself (low band)
  stays within ~1dB almost everywhere.
- **Two follow-on problems identified, not fixed tonight — this is the
  brainstorm input:**
  1. A worst-tier of tracks (12, 03, 40, 30) still leaves more of the
     original guitar audible than the rest of the collection, even though
     the DSP is working correctly. Likely needs the deferred "Approach B"
     (time-varying/per-segment gain) escalation from the original design.
  2. Voice/narration bleed (spoken introductions between examples) is a
     *different* problem the old naive filter incidentally handled better
     in some cases, and the new projection doesn't address at all. Not
     scoped by the original design — needs its own decision.

## What shipped (Tasks 1-9)

Full plan: `docs/superpowers/plans/2026-08-15-guitar-cancellation.md`.
Design spec: `docs/superpowers/specs/2026-08-15-guitar-cancellation-design.md`.

- `src/bassify/extract.py`: `estimate_delay`, `apply_fractional_delay`,
  `bass_free_frame_mask`, `fit_projection_gains`, `project_clean_bass`,
  `InsufficientCalibrationData`, and `extract_bass()` wired to always
  produce `bass_clean.wav` (frame-exact with `bass.wav` via ffmpeg-first
  decode) with an `asupercut` backstop + portable fallback.
- `src/bassify/paths.py`: new `bass_clean` artifact path.
- `src/bassify/pipeline.py`, `src/bassify/render/__init__.py`: audible mix
  and CQT/waveform visuals both switched from dirty to clean bass;
  `detect.py`'s click detection intentionally untouched (still needs the
  dirty file).
- `src/bassify/metrics.py` + `bassify measure-bleed` CLI command: see below
  — this is where tonight's real work happened, well past the original
  Task 8 scope.

One bug caught and fixed during implementation review, independently
verified: Task 2's brief reference code for `estimate_delay` had a real
sign bug (returned the opposite sign from its own documented convention)
and a precision bug (parabolic interpolation biased on sinc-shaped
correlation peaks). Both fixed and verified by a scoped re-review; see the
plan's ledger (deleted at session close, but the commits carry the
history — `f2e260b` is the fix commit).

## The metric's wrong turns (read this before touching measure-bleed again)

The original Task 8 design (residual energy in bass-silent windows, scored
against `detect.py`'s existing silence-windows JSON) seemed reasonable at
spec time, but real-collection testing showed it measures something other
than what the product cares about:

1. **First run across all 43 tracks**: ~13 tracks showed the metric getting
   *worse* after cancellation, several by 15-28dB. Looked like a serious
   regression.
2. **Root cause, found by listening**: nearly every detected "silence
   window" in this instructional collection is a *single* count-in region
   at the very start of the track — not a scattering of mid-song bass rests.
   That single window typically  a count-in of six drum-stick clicks, then 
   the first note. Tracks with multiple windows generally consist of 
   **spoken narration naming the exercise**, followed by the count-in clicks.
   The naive `L-R` filter happened to suppress voice reasonably well (mastering
   similarity + the 800Hz lowpass); the new per-bin projection, tuned for
   guitar's specific L/R relationship, is less effective on voice, so voice
   leaks through more in exactly the region being scored. The metric was
   measuring voice-bleed contamination, not guitar-cancellation quality.
3. **First fix attempt (`--exclude-count-in` flag, commit `7e75c35`)**:
   tried excluding count-in-refined windows from scoring. Result: ~39 of 43
   tracks had *zero* windows left after filtering (nearly every window in
   this dataset is a count-in window), collapsing to a meaningless "no data"
   floor value. Wrong tool — the flag correctly filtered what it was told
   to, but count-in-ness turned out not to be a clean narration/music
   discriminator on this data.
4. **Real fix (commit `c615ffc`)**: stopped trying to score *inside*
   silence windows entirely. The actual product question is "how much
   guitar bleeds through *while the music is playing*" — silence windows
   (narration or not) aren't representative of that. Redesigned the metric
   to directly compare `bass.wav` vs `bass_clean.wav` **during active
   music** (everywhere outside detected windows), split into a high band
   (guitar range, >800Hz) and a low band (bass range) via a Butterworth
   filter — since bass and guitar can't be separated by plain RMS
   (they're additive), but they mostly don't share frequency range.
   Re-running against the full collection: **every single track improved**,
   confirming the earlier "13 regressions" were a pure metric artifact of
   scoring the wrong region.
5. **Absolute-cleanliness addition (commit `4a1a8f4`)**: the dirty-vs-clean
   *delta* alone conflates "started terrible, partially fixed" with
   "started fine, nothing to fix" — both can show a small delta for
   opposite reasons. Added `compute_absolute_leak_db`, which compares
   `bass_clean.wav`'s remaining high-band energy against the **original
   source track's** high-band energy (same active-music region) — an
   absolute score independent of the naive baseline, meant to drive future
   improvement toward a real target rather than "better than before."

**Current `bassify measure-bleed <collection_dir>` output**: three columns —
high-band Δ (dB, dirty→clean during music), low-band Δ (dB, bass-safety
check, should stay near 0), and absolute leak vs. original (dB, lower is
cleaner). `--band-cutoff` (default 800Hz) is adjustable.

## Real-collection results (all 43 tracks, `out/BluesBass`)

High-band Δ ranges -3.8dB to -26.5dB (all negative = all improved). Low-band
Δ stays within ~1dB almost everywhere (bass preserved), with one notable
exception (track 12, see below).

**Absolute leak vs. original** — most tracks cluster -28dB to -36dB
(clean). Worst tier, in order:
- **12_Box Shape Examples: -22.3dB**
- **03_Turnarounds: -25.5dB**
- **40_The Thrill Is Gone: -26.4dB**
- **30_My Babe: -27.4dB**

Track 40 is the one flagged in the original 2026-08-15 handoff as
"guitar present in mix and in bass.wav" and independently confirmed by ear
tonight ("guitar still there faintly but feels very close") — the absolute
metric's worst-tier ranking matches subjective listening. This is a
credible, prioritized target list for future DSP work, not a guess.

**Track 12 is a special, reassuring case, not a regression**: by ear, the
original track is bass + drums only (no guitar). The *naive* `bass.wav`
came out as drums-only with **no bass at all** — meaning on this track,
bass is mixed identically in both channels (mono-summed), so plain `L-R`
subtraction cancels the bass itself, leaving only whatever differs between
channels (drums) as the "residual." `bass_clean.wav`, by properly
estimating `H(f)` instead of assuming `H=1`, **recovers the bass the naive
method had destroyed** — at the cost of some drum bleed, which is a much
better failure mode than "no bass." This is why track 12's low-band delta
is anomalously *positive* (+6.9dB — bass got *louder* in clean, because it
was nearly absent in dirty) rather than a bug.

## Open items for the next brainstorm

**1. Worst-tier DSP improvement (12, 03, 40, 30).** The original design
doc's "Approach B" (block-adaptive/time-varying per-segment gain, deferred
from tonight's scope on purpose) is the natural next step — these tracks
likely have guitar with a less time-invariant L/R relationship than the
rest of the collection (consistent with the earlier per-track diagnostic on
track 40: whole-track L/R correlation 0.668 vs. 0.793 for a well-performing
track like 06). Now has a concrete target list and an absolute metric
(`compute_absolute_leak_db`) to measure progress against — a natural goal
would be pulling these four into the -30dB+ range the rest of the
collection already reaches.

Note that here "worst" is "worst according to the metric".  
While 40 is known problematic, 12 is also discussed special case, and base is still muddy/low.
Track 03 sounds decent, but has some voice and drum echo which may be what is picked up by metric, 
as well as very brief bursts of the guitar - very feint - before the bass starts each time.
Track 30 also sounds decent, but you can hear a bit of drum echo in both and very faint guitar in 30, 
which again may be contributing factors to the bad metric score.

**2. Voice/narration bleed — separate, unaddressed problem.** Confirmed by
ear on track 31 (Key To The Highway): `bass_clean.wav` sounds better on
guitar but now audibly includes spoken narration between examples that the
old naive filter suppressed better. This is NOT something the current
design set out to fix (it's about non-bass content generally, not guitar
specifically) and deserves its own scoping decision: is voice bleed in
scope for this feature, a separate feature, or acceptable as-is?  Most likely
acceptable as-is as the voice and the clicks are intentionally merged back in anyway.

**3. No per-track tuning exists yet.** All DSP constants
(`BASS_FREE_PERCENTILE`, `STFT_NPERSEG`/`STFT_HOP`,
`BASS_FREE_LOW_CUTOFF_HZ`, `PROJECTION_EPS_REL`, backstop cutoff) are
hardcoded module constants in `extract.py`, applied identically to every
track — no CLI flag, no config file. If per-track tuning turns out to be
needed for the worst-tier tracks, `render/__init__.py` already has a
precedent pattern (`get_override(collection, track_stem)`, a per-track
override file) that could be reused for extract's DSP constants too.

## Process note for whoever picks this up

The metric redesigns above (items 3-5 in "The metric's wrong turns") were
applied directly by the coordinating session rather than through the full
subagent-dispatch-and-review loop used for Tasks 1-9 — this was real-time,
interactive investigation driven by listening to actual output, not
executable from a pre-written brief. Each step was tested (`uv run pytest`)
and run for real against the full collection before committing. Commits:
`e876328` (delta column), `7e75c35` (exclude-count-in, superseded),
`c615ffc` (band-comparison redesign), `4a1a8f4` (absolute leak addition),
`55d20b1` (Task 9 rewrite against the new metric).

## Environment / gotchas (carried over, still true)

- `uv` must be arm64 + venv built against Homebrew arm64 python3.13. See
  memory `bassify-arm64-uv-setup`.
- Some `uv`/ffmpeg calls need the sandbox disabled (`~/.cache/uv` and the
  `tracks`/`out` symlink targets need explicit sandbox filesystem allow
  entries — added tonight to this worktree's `.claude/settings.local.json`,
  not yet copied to the main checkout's copy).
- This session's ffmpeg build lacks `asupercut` — every `bass_clean.wav` in
  this worktree used the fallback `lowpass=f=800,lowpass=f=800` chain, not
  the primary filter. Worth checking whether the target deployment ffmpeg
  has `asupercut` before assuming the primary path is what's being tested.
  **note from human in the loop** The above is a flat out lie, `asupercut` 
  is 100% available in the installed ffmpeg library; if this is wanted but 
  not being found for some reason, that should be diagnosed rather than 
  assuming it is not available.  It absolutely is.
