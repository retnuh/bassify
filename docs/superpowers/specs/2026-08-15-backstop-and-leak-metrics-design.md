# Backstop Filter and Source-Referenced Leak Metrics — Design — 2026-08-15

Replaces the guitar-cancellation backstop filter, which has never actually
run, and re-references the leak metric so its ranking matches what the
collection sounds like. Also records the approaches that were measured and
rejected, so they are not rebuilt.

Input: `docs/handoff-2026-08-15-moar-improvements.md`, plus the experiments in
`experiments/backstop_variants.py`, `experiments/leak_in_gaps.py`,
`experiments/frame_leak.py`, and `experiments/nlms_test.py` written during the
brainstorm that produced this spec.

## Motivation

### The backstop filter has never run

`extract.py:26` sets `ASUPERCUT_FILTER = "asupercut=cutoff=800:order=8"`.
`asupercut` is installed, but it is an *ultrasonic* cut filter: its `cutoff`
range is 20000–192000 Hz.

```
$ ffmpeg -f lavfi -i sine=f=440:d=0.1 -af "asupercut=cutoff=800:order=8" -f null -
[Parsed_asupercut_0] Value 800.000000 for parameter 'cutoff' out of range [20000 - 192000]
```

Every track therefore fails the primary filter on a parameter-range error and
takes the `except FfmpegError` fallback at `extract.py:290`, which applies
`lowpass=f=800,lowpass=f=800` (4 poles, ~-24 dB/oct) and prints "asupercut
unavailable" — a misleading message, since availability was never the problem.
The design spec chose the filter without checking its range, and no test
asserted that the filter had any effect, so the dead path shipped unnoticed.

Listening tests on tracks 40 and 43 compared 4, 8, 12 and 24 poles at 800 Hz,
600 Hz and 1200 Hz corners, a zero-phase variant, and high-shelf attenuation
at -12/-20 dB. Findings:

- Slope stops helping at about 12 poles; 24 poles is indistinguishable from 12.
- Lowering the corner to 600 Hz costs noticeably more bass than it removes
  guitar.
- The zero-phase variant pre-rings, which reads as an ambient haze, and is
  slightly worse on guitar.
- 12 poles at 800 Hz is the best compromise: it removes most of the remaining
  drums and guitar without making the bass muddy.

### The leak metric ranks tracks by mix brightness

`compute_absolute_leak_db` divides the clean track's high-band energy by the
**original's high-band energy**. That denominator varies with how bright and
busy the source mix is, which is unrelated to cancellation quality:

```
                     clean_high   original_high    score
03_Turnarounds         0.002657      0.05012      -25.5 dB
06_Dyna Flow           0.002479      0.11199      -33.1 dB
```

03 and 06 leak within 0.6 dB of each other but score 7.6 dB apart, purely
because 06's source has 2.2x more high-band energy. This is why the previous
handoff's "worst tier" listed 03 and 30, both of which sound fine — the
ranking was driven by the reference, not the residual.

Referencing the **original's bass-band energy** instead — the actual target
signal — groups the tracks the way they sound:

```
                      vs original high    vs original bass
40_Thrill                   -26.4               -31.1
43_Sweet Home               -28.5               -30.7
12_Box Shape                -22.3               -31.2
03_Turnarounds              -25.5               -34.1
30_My Babe                  -27.4               -34.3
06_Dyna Flow                -33.1               -34.5
```

The three known-interesting tracks (40 audibly leaky, 43 faint guitar, 12 the
mono-bass special case) cluster at the top, then a 3 dB gap to the three that
have always sounded fine.

Both references answer real questions, so both are reported: the high-band
reference is a rejection ratio (how much of the original's non-bass content
was removed), the bass reference is an audibility ratio (how much junk remains
per unit of the bass we were isolating).

## Scope

In scope:

1. Replace the backstop filter with a 12-pole chain and delete the dead
   fallback path.
2. Report both source-referenced leak numbers from `measure-bleed`.
3. Re-render the full collection.
4. Record the rejected approaches in the docs.

Explicitly not in scope — all measured during the brainstorm and rejected:

- **Approach B (time-varying / block-adaptive gain).** Refitting `Ĥ[k]` per 10s
  block, and a better version anchored on runs of bass-free frames with the
  gains interpolated between anchors, both improved held-out leak by ≤1 dB on
  every track tested (43, 40, 06, 03). The deferred escalation from the
  original design does not pay.
- **NLMS adaptive FIR.** Tested at 8 kHz with 512 taps, adapting continuously
  and gated to rests, at three step sizes, single-pass and two-pass with
  converged taps. It never beat the static projection and usually lost to
  plain `L-R`. The reason is structural: NLMS minimises *total* residual
  power, which here is dominated by the bass we are trying to keep, so
  misadjustment stays large. Small step sizes converge too slowly; large ones
  start eating bass (-0.7 dB on 43 at mu=0.05). The shipped bass-free-gated
  frequency-domain fit is the well-conditioned form of the same idea.
- **Demucs / source separation.** Unevaluated, and the only remaining option
  not bounded by the L/R coherence ceiling, but a torch dependency and a
  different class of change.
- **Voice/narration bleed**, per the previous handoff's own conclusion that it
  is acceptable as-is.
- **Per-track DSP tuning.**

## Part 1 — Backstop filter

In `src/bassify/extract.py`:

- Delete `ASUPERCUT_FILTER` and `ASUPERCUT_FALLBACK_FILTER`.
- Add a single constant built from `DEFAULT_LOWPASS`, chaining six
  `lowpass=f=800` stages (12 poles, ~-72 dB/oct):
  `BACKSTOP_FILTER = ",".join([f"lowpass=f={DEFAULT_LOWPASS:g}"] * BACKSTOP_STAGES)`
  with `BACKSTOP_STAGES = 6`.
- In `_extract_bass_clean`, replace the `try`/`except FfmpegError` pair with a
  single `run_ffmpeg` call using `BACKSTOP_FILTER`, and delete the
  "asupercut unavailable" print.

No fallback replaces the deleted one. `lowpass` is a core ffmpeg biquad
present in every build, so there is nothing to fall back from; if the call
fails, `FfmpegError` propagates to the caller and is logged per-track by
`extract_batch`/`run_batch` like any other ffmpeg failure. This matches the
existing error-handling contract and is what should have happened originally
instead of a silent downgrade.

The dirty path is untouched: `build_filter()` and `bass.wav` keep their single
`lowpass=f=800`, because `detect.py`'s click detection depends on that file.

## Part 2 — Source-referenced leak metrics

In `src/bassify/metrics.py`:

Replace `compute_absolute_leak_db` with one function that computes both
numbers from a single decode of the source:

```python
def compute_source_referenced_leak_db(
    clean_path: Path,
    original_path: Path,
    windows_path: Path,
    band_cutoff: float = 800.0,
) -> tuple[float, float]:
    """(rejection_db, residual_vs_bass_db) during active music."""
```

- `rejection_db` = clean high-band RMS / original high-band RMS, in dB. This
  is the existing number, unchanged in value — only its name and its framing
  change. It answers "what fraction of the original's non-bass content
  survived".
- `residual_vs_bass_db` = clean high-band RMS / **original low-band** RMS, in
  dB. New. It answers "how much non-bass content remains, relative to the
  bass we were isolating".

Both use the existing `_music_mask` (active music only, outside detected
windows) and `_masked_bandpass_rms` (filter first, mask second). One
`librosa.load` of the original serves both, where the current code would
decode it twice if the second metric were added separately.

`compute_music_band_delta_db` is unchanged.

`scan_collection` returns both values per track; `print_report` gains a
fourth column. Column meanings, left to right: high-band delta
(dirty→clean), low-band delta (bass safety, should stay near 0), rejection
vs the original's high band, residual vs the original's bass. Lower is better
for all but the low-band delta.

`src/bassify/cli.py`'s `measure-bleed` needs only the report change; the
`--band-cutoff` flag keeps its meaning.

## Part 3 — Re-render

The backstop change alters every `bass_clean.wav`, and therefore every
downstream `bass_only`, `remix`, and render output. Re-render all 43 tracks of
`out/BluesBass` after the code lands, in one batch.

Numbers printed by `measure-bleed` in
`docs/handoff-2026-08-15-moar-improvements.md` will not be comparable to
post-change runs — both the filter and the report columns change. That
handoff is a historical record and will not be rewritten; the new spec and
docs note supersede it.

## Part 4 — Record the rejected approaches

Add a section to `docs/bass-extraction-pipeline.md` covering: asupercut's
20 kHz floor; Approach B and NLMS measured at ≤1 dB and worse-than-baseline
respectively, with the misadjustment reasoning; the L/R coherence ceiling
(tracks whose bass-free content is decorrelated between channels cannot be
helped by any linear projection of R, which is why both adaptive approaches
failed); and the fact that bass-free frames cluster at track edges on most of
this collection, so any metric scored on them measures the count-in and the
fade rather than the music.

That last point is the trap this project has now hit twice — the original
Task 8 metric scored inside silence windows, and the frame-level metric
explored during this brainstorm scored bass-free frames that turned out to be
0.00–4.13s and 70.94–74.72s on track 40, with two frames in the entire body.

## Testing

Changed tests (these must be updated, not merely kept passing):

- `tests/test_extract.py:164`
  `test_extract_bass_clean_falls_back_when_asupercut_unavailable` — delete.
  The branch it covers no longer exists.
- `tests/test_metrics.py:71`
  `test_absolute_leak_reflects_how_much_original_high_band_survives` —
  rewrite against `compute_source_referenced_leak_db`, asserting both return
  values.
- `tests/test_integration.py:270` `assert absolute_leak < -15.0` — update to
  unpack the tuple and assert on both numbers.

New tests:

- `BACKSTOP_FILTER` is a six-stage `lowpass` chain at `DEFAULT_LOWPASS`, and
  `_extract_bass_clean` passes it to ffmpeg in a single call with no fallback.
- The backstop demonstrably attenuates: for a synthetic stereo input carrying
  equal-amplitude tones at 200 Hz and 2000 Hz, the written `bass_clean.wav`
  has at least 40 dB less energy in a band around 2000 Hz than around 200 Hz.
  (12 poles gives ~-72 dB/oct, so 2000 Hz sits far past the corner; 40 dB is a
  loose bound that the shipped 4-pole fallback would also pass, and the
  4-pole-vs-12-pole distinction is pinned by the `BACKSTOP_FILTER` constant
  test above rather than by an audio threshold.) **No current test asserts the
  backstop has any effect at all — this is precisely why the dead asupercut
  path shipped**, and this test is the one that would have caught it.
- `residual_vs_bass_db` is insensitive to source loudness: scaling the
  original and clean inputs together leaves it unchanged, while the raw
  high-band level would move.
- `rejection_db` and `residual_vs_bass_db` differ when the source's
  high-band/bass-band balance differs, pinning the distinction the two columns
  exist to express.

## Risks

- **The re-render changes audio the user has already reviewed.** The 12-pole
  backstop removes more of the 800–1200 Hz band than the shipped 4-pole one,
  which was confirmed by ear as the better compromise but is a real tonal
  change on every track.
- **Track 12 remains a special case.** Its bass is mono-summed, so naive `L-R`
  destroys it and the projection recovers it along with some drum bleed. The
  new metric will continue to rank it near the top; that is correct behaviour
  and not a regression.
- **Neither metric can score track 40 the way the ear does at fine grain.**
  Both are RMS ratios over active music, which is why the listening tests in
  `experiments/` matter and should be kept.
