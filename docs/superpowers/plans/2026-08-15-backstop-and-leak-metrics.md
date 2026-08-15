# Backstop Filter and Source-Referenced Leak Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the guitar-cancellation backstop filter (which has never actually run) with a working 12-pole lowpass, re-reference the leak metric so its ranking matches what the collection sounds like, and record the approaches that were measured and rejected.

**Architecture:** Three independent code changes plus a docs change and a re-render. `extract.py` swaps a dead `asupercut` filter string and its silent fallback for one six-stage `lowpass` chain. `metrics.py` replaces `compute_absolute_leak_db` with a function returning two source-referenced numbers from a single decode of the original. `cli.py`'s report grows a column. Nothing changes in the dirty `bass.wav` path, which `detect.py` depends on.

**Tech Stack:** Python 3.13, numpy, scipy, soundfile, librosa, typer, ffmpeg (CLI), pytest, ruff, uv.

**Global Constraints:**
- `DEFAULT_LOWPASS` stays `800.0`. The backstop is six chained `lowpass=f=800` stages (12 poles, ~-72 dB/oct) — not 4, not 24; this was settled by listening tests.
- The dirty path is untouched: `build_filter()`, `bass.wav`, and `detect.py` must behave exactly as before. `detect.py`'s click detection depends on the dirty file.
- No fallback filter and no `try`/`except FfmpegError` around the backstop. `FfmpegError` must propagate.
- Both leak numbers are computed from ONE `librosa.load` of the original track, not two.
- Never hand-format Python. Run `uv run ruff format .` and `uv run ruff check --fix .` (see `AGENTS.md`).
- `uv run ruff format --check .` runs over the WHOLE repo — any drift anywhere fails the gate.
- Some commands need `dangerouslyDisableSandbox: true` (uv cache, ffmpeg, `tracks/` and `out/` symlink targets).

**User decisions (already made):**
- "C 12 pole is probably best compromise, base not too muddy and still kills most of the rest of band - drums, guitar" — 12 poles at 800 Hz, chosen by ear over 4, 8, 24 poles, a 600 Hz corner, a 1200 Hz corner, zero-phase, and shelf variants.
- "we should be measuring against the original track in someway, it's the only meaningful numbers. comparing two derived things against each other is meaningless" — hence both metrics reference the source track, and the raw-dBFS option was rejected.
- Report **both** references: rejection vs the original's high band, and residual vs the original's bass.
- Re-render scope: "Whole collection at once" — all 43 tracks.
- Approach B (time-varying gain) and NLMS are rejected on measurement, not to be built.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/bassify/extract.py` | DSP + ffmpeg invocation for `bass.wav` / `bass_clean.wav` | Replace 2 filter constants with 2 new ones; delete fallback branch in `_extract_bass_clean` |
| `src/bassify/metrics.py` | Leak measurement | Replace `compute_absolute_leak_db` with `compute_source_referenced_leak_db`; widen `scan_collection` rows and `print_report` |
| `src/bassify/cli.py` | CLI surface | Docstring for `measure-bleed` only |
| `tests/test_extract.py` | Extract unit tests | Delete the fallback test; add 2 new tests |
| `tests/test_metrics.py` | Metrics unit tests | Rewrite 1 test; add 2 new tests |
| `tests/test_integration.py` | Real-track regression | Update to the new metric signature |
| `docs/bass-extraction-pipeline.md` | Pipeline reference doc | New section recording rejected approaches |

---

## Task 1: Replace the dead asupercut backstop with a 12-pole lowpass chain

**Goal:** `bass_clean.wav` is filtered by a working six-stage `lowpass=f=800` chain, with the dead `asupercut` primary and its silent fallback removed.

**Files:**
- Modify: `src/bassify/extract.py:26-27` (constants), `src/bassify/extract.py:277-303` (`_extract_bass_clean` tail)
- Test: `tests/test_extract.py:164-197` (delete), plus new tests in the same file

**Acceptance Criteria:**
- [ ] `ASUPERCUT_FILTER` and `ASUPERCUT_FALLBACK_FILTER` no longer exist in `extract.py`
- [ ] `BACKSTOP_STAGES == 6` and `BACKSTOP_FILTER` is six comma-separated `lowpass=f=800` stages
- [ ] `_extract_bass_clean` makes exactly one backstop ffmpeg call, with no `try`/`except FfmpegError` and no "asupercut unavailable" print
- [ ] `FfmpegError` from the backstop call propagates to the caller
- [ ] `test_extract_bass_clean_falls_back_when_asupercut_unavailable` is deleted
- [ ] A test proves the backstop actually attenuates content above the cutoff

**Verify:** `uv run pytest tests/test_extract.py -v` → all pass, no test named `*asupercut*` collected

**Steps:**

- [ ] **Step 1: Delete the obsolete fallback test**

Remove `tests/test_extract.py:164-197` entirely — the whole `test_extract_bass_clean_falls_back_when_asupercut_unavailable` function. The branch it covers is being deleted, so the test cannot be adapted; it must go.

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_extract.py`:

```python
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
    import pytest

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
```

`numpy` is already imported as `np` at the top of `tests/test_extract.py`; `pytest` is imported inside the test to keep the diff local if the module-level import is absent — check the file header and drop the inner import if `pytest` is already imported there.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_extract.py -k "backstop" -v`
Expected: FAIL — `AttributeError: module 'bassify.extract' has no attribute 'BACKSTOP_STAGES'`

- [ ] **Step 4: Replace the constants**

In `src/bassify/extract.py`, replace lines 26-27:

```python
ASUPERCUT_FILTER = f"asupercut=cutoff={DEFAULT_LOWPASS:g}:order=8"
ASUPERCUT_FALLBACK_FILTER = f"lowpass=f={DEFAULT_LOWPASS:g},lowpass=f={DEFAULT_LOWPASS:g}"
```

with:

```python
# Backstop for bass_clean.wav: six chained 2-pole lowpass stages = 12 poles,
# ~-72 dB/oct. NOT asupercut -- that filter's cutoff range is 20000-192000 Hz
# (it cuts ultrasonic content), so asupercut=cutoff=800 fails a parameter
# check on every track. 12 poles was chosen by listening tests over 4, 8 and
# 24 poles and over 600/1200 Hz corners: it removes most of the remaining
# drums and guitar without making the bass muddy.
BACKSTOP_STAGES = 6
BACKSTOP_FILTER = ",".join([f"lowpass=f={DEFAULT_LOWPASS:g}"] * BACKSTOP_STAGES)
```

- [ ] **Step 5: Remove the fallback branch**

In `_extract_bass_clean`, replace the whole `try`/`except FfmpegError` block (`src/bassify/extract.py:277-303`) with a single call:

```python
        run_ffmpeg(
            [
                "-i",
                str(tmp_bass),
                "-af",
                BACKSTOP_FILTER,
                "-vn",
                "-c:a",
                "pcm_s24le",
                str(out_clean),
            ]
        )
```

Then update the function docstring: replace "apply the asupercut backstop (with fallback) via ffmpeg" with "apply the 12-pole lowpass backstop via ffmpeg". Remove the now-unused `FfmpegError` import from `extract.py` **only if** nothing else in the file uses it — grep first: `grep -n FfmpegError src/bassify/extract.py`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_extract.py -v`
Expected: PASS, and no `asupercut` test collected.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff format . && uv run ruff check --fix .
git add src/bassify/extract.py tests/test_extract.py
git commit -m "fix(extract): replace dead asupercut backstop with a 12-pole lowpass chain

asupercut's cutoff range is 20000-192000 Hz, so asupercut=cutoff=800 failed a
parameter check on every track and silently took the 4-pole fallback while
printing 'asupercut unavailable'. Availability was never the problem.

Replaces it with six chained lowpass=f=800 stages (12 poles), chosen by
listening tests, and drops the fallback so ffmpeg failures propagate."
```

---

## Task 2: Prove the backstop actually attenuates

**Goal:** An end-to-end test asserts the written `bass_clean.wav` really is lowpassed — the check whose absence let a dead filter ship.

**Files:**
- Test: `tests/test_extract.py` (new test)

**Acceptance Criteria:**
- [ ] A test builds a stereo input carrying equal-amplitude 200 Hz and 2000 Hz tones, runs the real extract path, and asserts the output's energy around 2000 Hz is at least 40 dB below its energy around 200 Hz
- [ ] The test is skipped when ffmpeg is unavailable, matching the existing integration-test convention
- [ ] The test fails if the backstop call is removed (verified by temporarily removing it)

**Verify:** `uv run pytest tests/test_extract.py -k attenuat -v` → PASS

**Steps:**

- [ ] **Step 1: Check the existing ffmpeg-skip convention**

Run: `grep -n "ffmpeg_missing\|skip_reason" tests/test_integration.py | head -5`

Reuse the same skip pattern in the new test rather than inventing one. If `ffmpeg_missing` lives only in `tests/test_integration.py`, put this test there instead of `tests/test_extract.py` and note that in the commit.

- [ ] **Step 2: Write the failing test**

```python
def test_bass_clean_backstop_attenuates_above_the_cutoff(tmp_path, monkeypatch):
    """The written bass_clean.wav must actually be lowpassed.

    No test asserted this before, which is why the asupercut backstop could
    fail on every single track without anything going red. A 2000 Hz tone
    sits well past the 800 Hz corner; at 12 poles it should be crushed
    relative to a 200 Hz tone of equal input amplitude.

    The 40 dB bound is deliberately loose -- the 4-pole fallback would pass
    it too. Distinguishing 4 poles from 12 is the job of the
    BACKSTOP_FILTER constant test; this test's job is proving the filter
    runs at all.
    """
    import numpy as np
    import soundfile as sf
    from scipy.signal import butter, sosfiltfilt

    from bassify.extract import extract_bass
    from bassify.paths import resolve_paths

    sr = 44100
    t = np.arange(sr * 3) / sr
    low_tone = 0.3 * np.sin(2 * np.pi * 200 * t)
    high_tone = 0.3 * np.sin(2 * np.pi * 2000 * t)
    # L carries both; R carries only the high tone, so the projection has a
    # reference to cancel and the low tone survives as "bass".
    stereo = np.stack([low_tone + high_tone, high_tone], axis=1)

    src = tmp_path / "tones.wav"
    sf.write(str(src), stereo, sr, subtype="PCM_24")

    monkeypatch.chdir(tmp_path)
    extract_bass(src, force=True)
    clean_path = resolve_paths(src).bass_clean

    y, out_sr = sf.read(str(clean_path), dtype="float64", always_2d=False)

    def band_rms(low, high):
        sos = butter(4, [low, high], btype="bandpass", fs=out_sr, output="sos")
        return float(np.sqrt(np.mean(sosfiltfilt(sos, y) ** 2)))

    low_rms = band_rms(150, 250)
    high_rms = band_rms(1800, 2200)
    ratio_db = 20 * np.log10(max(high_rms, 1e-12) / max(low_rms, 1e-12))

    assert ratio_db < -40, (
        f"backstop did not attenuate: 2kHz is only {ratio_db:.1f}dB below 200Hz"
    )
```

Add the ffmpeg skip decorator found in Step 1 above this function.

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_extract.py -k attenuat -v`
Expected: PASS (Task 1 already installed a working filter).

- [ ] **Step 4: Prove the test has teeth**

Temporarily comment out the `run_ffmpeg([... BACKSTOP_FILTER ...])` call in `_extract_bass_clean` and replace it with a plain copy of `tmp_bass` to `out_clean`:

```python
        import shutil
        shutil.copy(tmp_bass, out_clean)
```

Run: `uv run pytest tests/test_extract.py -k attenuat -v`
Expected: **FAIL** — this is the proof the test would have caught the original bug.

Then revert the change (`git checkout src/bassify/extract.py`) and re-run to confirm PASS. Record both outcomes in the task report.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format . && uv run ruff check --fix .
git add tests/
git commit -m "test(extract): assert the bass_clean backstop actually attenuates

No test checked that the backstop had any effect, which is how a filter that
failed on every track shipped unnoticed. Verified this test fails when the
backstop call is removed."
```

---

## Task 3: Replace absolute-leak with two source-referenced metrics

**Goal:** `metrics.py` reports both a rejection ratio (vs the original's high band) and a residual ratio (vs the original's bass), from a single decode of the source.

**Files:**
- Modify: `src/bassify/metrics.py:103-136` (function), `:139-177` (`scan_collection`), `:180-187` (`print_report`)
- Test: `tests/test_metrics.py:71-98` (rewrite), plus new tests

**Acceptance Criteria:**
- [ ] `compute_absolute_leak_db` is gone; `compute_source_referenced_leak_db` returns `tuple[float, float]` as `(rejection_db, residual_vs_bass_db)`
- [ ] `rejection_db` = clean high-band RMS / original high-band RMS, in dB — numerically identical to the old `compute_absolute_leak_db` for the same inputs
- [ ] `residual_vs_bass_db` = clean high-band RMS / original **low**-band RMS, in dB
- [ ] The original track is decoded exactly once per call
- [ ] `scan_collection` rows carry both values; `print_report` prints four numeric columns
- [ ] `compute_music_band_delta_db` and `_masked_bandpass_rms` are unchanged

**Verify:** `uv run pytest tests/test_metrics.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_metrics.py:71-98` (`test_absolute_leak_reflects_how_much_original_high_band_survives`) with:

```python
def test_rejection_and_residual_both_reflect_how_much_guitar_survives(tmp_path):
    """Both source-referenced numbers must separate 'fully cleaned' from
    'partially cleaned'."""
    sr = 8000
    n = sr * 4
    t = np.arange(n) / sr
    bass = 0.3 * np.sin(2 * np.pi * 80 * t)
    guitar = 0.3 * np.sin(2 * np.pi * 2000 * t)

    original_path = tmp_path / "original.wav"
    sf.write(str(original_path), bass + guitar, sr, subtype="PCM_24")
    windows_path = _write_windows(tmp_path, [{"start": 0.0, "end": 1.0}])

    clean_full_path = tmp_path / "bass_clean_full.wav"
    sf.write(str(clean_full_path), bass, sr, subtype="PCM_24")

    clean_partial_path = tmp_path / "bass_clean_partial.wav"
    sf.write(str(clean_partial_path), bass + 0.5 * guitar, sr, subtype="PCM_24")

    rej_full, res_full = compute_source_referenced_leak_db(
        clean_full_path, original_path, windows_path
    )
    rej_partial, res_partial = compute_source_referenced_leak_db(
        clean_partial_path, original_path, windows_path
    )

    assert rej_full < -15
    assert rej_partial > rej_full
    assert rej_partial < -1

    assert res_full < -15
    assert res_partial > res_full


def test_residual_vs_bass_is_insensitive_to_source_loudness(tmp_path):
    """Scaling the original and the clean output together must not move
    residual_vs_bass_db.

    This is the property that makes it a fair cross-track score: a quietly
    mastered track must not look cleaner than a loud one just for being
    quiet. (The raw high-band level, considered and rejected during design,
    fails this.)
    """
    sr = 8000
    n = sr * 4
    t = np.arange(n) / sr
    bass = 0.3 * np.sin(2 * np.pi * 80 * t)
    guitar = 0.3 * np.sin(2 * np.pi * 2000 * t)
    windows_path = _write_windows(tmp_path, [{"start": 0.0, "end": 1.0}])

    results = []
    for tag, gain in (("loud", 1.0), ("quiet", 0.1)):
        orig_p = tmp_path / f"orig_{tag}.wav"
        clean_p = tmp_path / f"clean_{tag}.wav"
        sf.write(str(orig_p), gain * (bass + guitar), sr, subtype="PCM_24")
        sf.write(str(clean_p), gain * (bass + 0.3 * guitar), sr, subtype="PCM_24")
        results.append(compute_source_referenced_leak_db(clean_p, orig_p, windows_path))

    (_, res_loud), (_, res_quiet) = results
    assert abs(res_loud - res_quiet) < 0.5


def test_rejection_and_residual_differ_when_source_band_balance_differs(tmp_path):
    """The two columns exist because they answer different questions.

    Two sources with identical bass but different amounts of guitar, given
    identical clean output, must produce different rejection scores while
    residual-vs-bass stays put. This is exactly why the old single ratio
    ranked 03 as 'worst tier' when it leaked no more than 06 -- 06's source
    was simply brighter.
    """
    sr = 8000
    n = sr * 4
    t = np.arange(n) / sr
    bass = 0.3 * np.sin(2 * np.pi * 80 * t)
    # Amplitudes chosen so the "bright" source (bass + 4.0*guitar) peaks at 0.9:
    # PCM_24 clamps to +/-1, and clipping the write corrupts the low band that
    # residual_vs_bass_db divides by.
    guitar = 0.15 * np.sin(2 * np.pi * 2000 * t)
    windows_path = _write_windows(tmp_path, [{"start": 0.0, "end": 1.0}])

    clean_p = tmp_path / "clean.wav"
    sf.write(str(clean_p), bass + 0.1 * guitar, sr, subtype="PCM_24")

    dim_p = tmp_path / "orig_dim.wav"
    bright_p = tmp_path / "orig_bright.wav"
    sf.write(str(dim_p), bass + guitar, sr, subtype="PCM_24")
    sf.write(str(bright_p), bass + 4.0 * guitar, sr, subtype="PCM_24")

    rej_dim, res_dim = compute_source_referenced_leak_db(clean_p, dim_p, windows_path)
    rej_bright, res_bright = compute_source_referenced_leak_db(clean_p, bright_p, windows_path)

    assert rej_bright < rej_dim - 6  # brighter source -> flattering rejection score
    assert abs(res_bright - res_dim) < 0.5  # residual-vs-bass unmoved
```

Update the import block at `tests/test_metrics.py:8-12` to import `compute_source_referenced_leak_db` instead of `compute_absolute_leak_db`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_metrics.py -v`
Expected: FAIL — `ImportError: cannot import name 'compute_source_referenced_leak_db'`

- [ ] **Step 3: Replace the metric function**

Replace `src/bassify/metrics.py:103-136` with:

```python
def compute_source_referenced_leak_db(
    clean_path: Path,
    original_path: Path,
    windows_path: Path,
    band_cutoff: float = 800.0,
) -> tuple[float, float]:
    """How much non-bass content survives in bass_clean.wav, measured
    against the ORIGINAL track during active music.

    Returns (rejection_db, residual_vs_bass_db):

    - rejection_db: clean high-band energy relative to the ORIGINAL's
      high-band energy. A true rejection ratio -- "what fraction of the
      source's guitar/drums did we remove". A DSP performance number.

    - residual_vs_bass_db: clean high-band energy relative to the
      ORIGINAL's LOW-band (bass) energy -- "how much junk is left per unit
      of the bass we were isolating". An audibility number.

    Both are referenced to the source, because comparing two derived files
    against each other says nothing about how much of the original was
    actually removed. They differ because the original's high-band energy
    varies with how bright and busy the mix is, which has nothing to do
    with cancellation quality: on the real collection, tracks 03 and 06
    leaked within 0.6 dB of each other but scored 7.6 dB apart on
    rejection alone, purely because 06's source is brighter. Referencing
    the bass instead groups tracks the way they sound.

    Lower (more negative) is better for both.
    """
    yc, sr = sf.read(str(clean_path), dtype="float64", always_2d=False)
    yo = librosa.load(str(original_path), sr=sr, mono=True)[0].astype(np.float64)

    n = min(len(yc), len(yo))
    yc, yo = yc[:n], yo[:n]

    music_mask = _music_mask(n, sr, windows_path)

    clean_high = _masked_bandpass_rms(yc, music_mask, sr, low=band_cutoff, high=None)
    original_high = _masked_bandpass_rms(yo, music_mask, sr, low=band_cutoff, high=None)
    original_low = _masked_bandpass_rms(yo, music_mask, sr, low=None, high=band_cutoff)

    rejection = 20 * np.log10(max(clean_high / max(original_high, 1e-12), 1e-12))
    residual = 20 * np.log10(max(clean_high / max(original_low, 1e-12), 1e-12))
    return float(rejection), float(residual)
```

- [ ] **Step 4: Widen scan_collection and print_report**

In `scan_collection`, change the return annotation to
`list[tuple[str, float, float, float | None, float | None]]`, change the local `rows` annotation to match, and replace the `absolute_leak = ...` block with:

```python
        original_path = next(tracks_dir.glob(f"{track_dir.name}.*"), None)
        if original_path is not None:
            rejection, residual = compute_source_referenced_leak_db(
                bass_clean_path, original_path, windows_path, band_cutoff=band_cutoff
            )
        else:
            rejection, residual = None, None
        rows.append((track_dir.name, high_delta, low_delta, rejection, residual))
```

Update the docstring's last paragraph to say both values are `None` when the source track can't be found.

Replace `print_report` with:

```python
def print_report(rows: list[tuple[str, float, float, float | None, float | None]]) -> None:
    print(
        f"{'track':<45} {'high-band Δ (dB)':>16} {'low-band Δ (dB)':>16} "
        f"{'rejection (dB)':>15} {'residual/bass (dB)':>19}"
    )
    for name, high_delta, low_delta, rejection, residual in rows:
        rej_str = f"{rejection:.1f}" if rejection is not None else "n/a"
        res_str = f"{residual:.1f}" if residual is not None else "n/a"
        print(
            f"{name:<45} {high_delta:>16.1f} {low_delta:>16.1f} "
            f"{rej_str:>15} {res_str:>19}"
        )
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_metrics.py -v`
Expected: PASS

- [ ] **Step 6: Update the CLI docstring**

In `src/bassify/cli.py:322-330`, extend the `measure_bleed` docstring to describe all four columns. Add after the existing text:

```
    Also reports two source-referenced numbers: rejection (how much of the
    ORIGINAL track's high-band content was removed) and residual/bass (how
    much high-band content remains, relative to the original's bass level).
    Rejection is a DSP-performance number; residual/bass tracks audibility
    and is the one that ranks tracks the way they sound.
```

No signature change — `--band-cutoff` keeps its meaning.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff format . && uv run ruff check --fix .
git add src/bassify/metrics.py src/bassify/cli.py tests/test_metrics.py
git commit -m "feat(metrics): report two source-referenced leak numbers

The single absolute-leak ratio divided by the ORIGINAL's high-band energy,
which varies with how bright the source mix is. Tracks 03 and 06 leaked
within 0.6dB of each other but scored 7.6dB apart, putting tracks that
always sounded fine in the 'worst tier'.

Reports rejection (vs original high band, a DSP-performance number) and
residual/bass (vs original bass, an audibility number that matches what the
collection sounds like), from a single decode of the source."
```

---

## Task 4: Update the real-track regression test

**Goal:** The integration regression test uses the new metric and asserts on both numbers.

**Files:**
- Modify: `tests/test_integration.py:219-226` (comment), `:248`, `:258`, `:260-273`

**Acceptance Criteria:**
- [ ] The test imports and calls `compute_source_referenced_leak_db`, unpacking both values
- [ ] Both `rejection` and `residual` are asserted with thresholds that pass on tracks 06 and 40
- [ ] The printed diagnostic line includes both numbers
- [ ] The explanatory comment block reflects the current metric, not the removed one

**Verify:** `uv run pytest tests/test_integration.py -k known_bad -v` → PASS for both tracks (or SKIP if the real tracks are absent)

**Steps:**

- [ ] **Step 1: Get real threshold values before writing assertions**

Do NOT guess thresholds. Run the measurement first:

```bash
uv run python -c "
import sys
from pathlib import Path
sys.path.insert(0, 'src')
from bassify.metrics import compute_source_referenced_leak_db
for t in ['06_Dyna Flow', '40_The Thrill Is Gone']:
    d = Path('out/BluesBass') / t
    src = next(Path('tracks/BluesBass').glob(f'{t}.*'))
    w = d / f'{t}_silence_windows.json'
    print(t, compute_source_referenced_leak_db(d / f'{t}_bass_clean.wav', src, w))
"
```

Note: this reads the CURRENT `bass_clean.wav` files, which still have the 4-pole backstop until Task 5 re-renders. The 12-pole backstop will make both numbers *more* negative, so thresholds set from these values stay valid — but leave real headroom (round the observed value up by ~5 dB toward zero) so the test is not brittle.

- [ ] **Step 2: Update the imports and the call**

At `tests/test_integration.py:248`:

```python
    from bassify.metrics import compute_music_band_delta_db, compute_source_referenced_leak_db
```

At `:258`:

```python
    rejection, residual = compute_source_referenced_leak_db(p.bass_clean, src, windows)
```

- [ ] **Step 3: Update the print and assertions**

Replace `:260-273` with (substituting the thresholds derived in Step 1 for `REJ` and `RES`):

```python
    print(
        f"{track_name}: high-band Δ={high_delta:.1f}dB low-band Δ={low_delta:.1f}dB "
        f"rejection={rejection:.1f}dB residual/bass={residual:.1f}dB"
    )

    assert high_delta < 0, f"{track_name}: high-band leak did not improve (Δ={high_delta:.1f}dB)"
    assert abs(low_delta) < 5.0, (
        f"{track_name}: bass itself moved too much (low-band Δ={low_delta:.1f}dB) "
        "-- projection may be damaging bass, not just cancelling guitar"
    )
    assert rejection < REJ, (
        f"{track_name}: not enough of the original's high-band content was removed "
        f"(rejection={rejection:.1f}dB, need < {REJ}dB)"
    )
    assert residual < RES, (
        f"{track_name}: too much high-band content remains relative to the bass "
        f"(residual/bass={residual:.1f}dB, need < {RES}dB)"
    )
```

- [ ] **Step 4: Update the explanatory comment**

Rewrite `tests/test_integration.py:219-226` so it describes the current metric. Keep the history — it is the reason the metric looks the way it does:

```python
# Scored with metrics.compute_music_band_delta_db (dirty-vs-clean band
# comparison during active music) and compute_source_referenced_leak_db
# (rejection vs the original's high band, plus residual vs the original's
# bass).
#
# Two earlier designs were discarded. The first scored residual energy
# INSIDE detect.py's silence windows, which on this instructional collection
# are almost entirely count-in and narration -- it measured voice bleed, not
# guitar bleed. The second divided by the original's high-band energy alone,
# which ranks tracks by how bright the source mix is: 03 and 06 leak within
# 0.6dB of each other but scored 7.6dB apart. See the backstop-and-leak-
# metrics design doc for the full investigation.
```

- [ ] **Step 5: Run and commit**

Run: `uv run pytest tests/test_integration.py -k known_bad -v`
Expected: PASS (or SKIP if tracks/ffmpeg absent — if SKIPPED, say so explicitly in the task report rather than claiming a pass).

```bash
uv run ruff format . && uv run ruff check --fix .
git add tests/test_integration.py
git commit -m "test(integration): score the regression tracks with both source-referenced metrics"
```

---

## Task 5: Full-gate check and collection re-render

**Goal:** The whole repo gate is green, and all 43 tracks are regenerated with the new backstop.

**Files:** none modified (this task runs commands and reports numbers)

**Acceptance Criteria:**
- [ ] `just check` passes (lint + format-check + full test suite)
- [ ] All 43 tracks under `out/BluesBass` are regenerated with the 12-pole backstop
- [ ] `bassify measure-bleed out/BluesBass` runs and prints four columns for every track
- [ ] The before/after `measure-bleed` output is captured in the task report

**Verify:** `just check` → exit 0; `uv run bassify measure-bleed out/BluesBass` → 43 rows, four numeric columns

**Steps:**

- [ ] **Step 1: Capture the BEFORE measurement**

Before re-rendering, record the current numbers (they describe 4-pole output):

```bash
uv run bassify measure-bleed out/BluesBass | tee /tmp/measure-before.txt
```

Needs `dangerouslyDisableSandbox: true`.

- [ ] **Step 2: Run the full gate**

```bash
just check
```

Expected: exit 0. `ruff format --check .` covers the whole repo, so unrelated drift anywhere fails it — if it does, run `uv run ruff format .` and commit that separately rather than hand-editing.

- [ ] **Step 3: Re-render the collection**

```bash
uv run bassify run tracks/BluesBass --force
uv run bassify render out/BluesBass --force
```

Needs `dangerouslyDisableSandbox: true` (uv cache, ffmpeg, and the `tracks`/`out` symlink targets). This takes a while — the render step is roughly 0.5-2x realtime per track.

Per-track failures are logged and skipped by `run_batch`/`render_batch` rather than aborting. Collect any failures and report them; do not silently ignore them.

- [ ] **Step 4: Capture the AFTER measurement and compare**

```bash
uv run bassify measure-bleed out/BluesBass | tee /tmp/measure-after.txt
diff /tmp/measure-before.txt /tmp/measure-after.txt
```

Expected direction: the high-band delta and both source-referenced numbers get **more negative** (the 12-pole backstop removes more above 800 Hz); the low-band delta should barely move. Report the actual spread, including any track that moved the wrong way.

- [ ] **Step 5: Commit nothing, report everything**

`out/` is gitignored, so there is nothing to commit here. The deliverable is the before/after comparison in the task report.

---

## Task 6: Record the rejected approaches

**Goal:** `docs/bass-extraction-pipeline.md` explains what was measured and rejected, so it is not rebuilt.

**Files:**
- Modify: `docs/bass-extraction-pipeline.md` (new section after `### Caveat: imperfect cancellation`, which ends at line 51)

**Acceptance Criteria:**
- [ ] A new subsection covers: asupercut's 20 kHz floor, Approach B, NLMS, the coherence ceiling, and the edge-clustering of bass-free frames
- [ ] Each rejection cites the measurement that produced it, not just the conclusion
- [ ] The experiment scripts under `experiments/` are named so the measurements can be re-run

**Verify:** `grep -c "asupercut\|coherence\|NLMS" docs/bass-extraction-pipeline.md` → at least 3

**Steps:**

- [ ] **Step 1: Add the section**

Insert after the `### Caveat: imperfect cancellation` subsection (before `## 2. Detect the gaps` at line 52):

````markdown
### What was tried and rejected

Measured during the 2026-08-15 backstop investigation. Scripts live in
`experiments/`; re-run them before overturning any of this.

**`asupercut` is not a lowpass.** Its `cutoff` range is 20000-192000 Hz — it
cuts ultrasonic content. `asupercut=cutoff=800` fails a parameter check on
every track:

```
[Parsed_asupercut_0] Value 800.000000 for parameter 'cutoff' out of range [20000 - 192000]
```

The backstop is six chained `lowpass=f=800` stages (12 poles). Listening
tests (`experiments/backstop_variants.py`) compared 4, 8, 12 and 24 poles,
600/800/1200 Hz corners, a zero-phase variant, and -12/-20 dB high shelves.
Slope stops helping past 12 poles; a 600 Hz corner costs more bass than it
removes guitar; the zero-phase variant pre-rings audibly.

**Time-varying gain ("Approach B") — rejected, ≤1 dB.** Refitting `Ĥ[k]` per
10s block, and a better version anchored on runs of bass-free frames with
gains interpolated between anchors, both improved held-out leak by at most
1 dB on tracks 43, 40, 06 and 03 (`experiments/frame_leak.py`).

**NLMS adaptive FIR — rejected, worse than the baseline.** Tested at 8 kHz
with 512 taps, adapting continuously and gated to rests, at three step
sizes, single-pass and two-pass from converged taps
(`experiments/nlms_test.py`). It never beat the static projection and
usually lost to plain `L-R`. The reason is structural: NLMS minimises
*total* residual power, which here is dominated by the bass we are trying to
keep, so misadjustment stays large. Small step sizes converge too slowly;
large ones start eating bass (-0.7 dB on track 43 at mu=0.05). The shipped
bass-free-gated frequency-domain fit is the well-conditioned form of the
same idea.

**Why both adaptive approaches failed: the coherence ceiling.** Any
projection of R onto L can only remove content that is *correlated* between
channels. Energy-weighted `1 - coherence` over bass-free frames ranks tracks
by how much is cancellable at all, and that ranking predicts which tracks
the projection helps: 06 and 43 (ceiling ~-20 dB) gained 6-9 dB, while 40,
03 and 30 (~-11 dB) gained little or nothing. Their residual is decorrelated
— reverb tails, stereo-widened guitar — and no linear filter of R can touch
it. Improving those tracks needs something that is not a linear L/R
projection (spectral masking, or a separation model such as Demucs).

**Bass-free frames cluster at track edges.** On most of this collection the
bass plays continuously through the body, so frames detected as bass-free
are the count-in and the fade. Track 40's are `0.00-4.13s` and
`70.94-74.72s`, with two frames in the entire middle. Any metric scored on
bass-free content therefore measures the intro and the outro on most tracks
— the trap that sank the original silence-window metric and the frame-level
metric explored later. Only tracks with genuine mid-song rests (03, 43) can
be scored that way.
````

- [ ] **Step 2: Verify and commit**

Run: `grep -n "asupercut\|coherence\|NLMS" docs/bass-extraction-pipeline.md | head`
Expected: matches in the new section.

```bash
git add docs/bass-extraction-pipeline.md
git commit -m "docs: record the backstop and cancellation approaches that were rejected

asupercut's 20kHz floor, Approach B at <=1dB, NLMS losing to the baseline and
why, the L/R coherence ceiling that explains both failures, and the
edge-clustering of bass-free frames that has now misled two metric designs."
```

---

## Self-Review

**Spec coverage:**
- Part 1 (backstop) → Tasks 1, 2
- Part 2 (metrics) → Task 3, with the integration test in Task 4
- Part 3 (re-render) → Task 5
- Part 4 (document rejected approaches) → Task 6
- Spec's three named test changes → Task 1 Step 1 (delete), Task 3 Step 1 (rewrite), Task 4 (integration)
- Spec's four new tests → Task 1 Step 2 (constant), Task 2 (attenuation), Task 3 Step 1 (loudness invariance, band-balance divergence)

**Type consistency:** `compute_source_referenced_leak_db` returns `tuple[float, float]` in Task 3 and is unpacked as two values in Tasks 3, 4 and 5. `scan_collection` rows are 5-tuples in both `scan_collection` and `print_report`. `BACKSTOP_FILTER`/`BACKSTOP_STAGES` are named identically in Tasks 1, 2 and 6.

**Ordering:** Task 4's thresholds are measured from pre-re-render files (Step 1 notes this and requires headroom), so Task 4 does not depend on Task 5. Task 5 depends on Tasks 1-4 being committed and green.
