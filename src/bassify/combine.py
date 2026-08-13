"""Combine bass (ducked in gaps) + gated original (with fade) into a single mono mix.

Filtergraph strategy:
- Original gate: pass [window_start, cutoff] with a linear fade-out over the last
  `fade` seconds ending at cutoff.  Nothing passes after cutoff.
- Bass duck (count-in windows with last_click): bass is zeroed ALL the way from
  window_start to bass_onset in ffmpeg; the numpy post-step applies an even
  linspace(0->1) ramp from last_click to bass_onset, so bass rises smoothly under
  and after the donor click with zero dead-silence cliff.
- Bass duck (fallback windows without last_click): gain = 0 during
  [window_start, bass_onset - rampup], linear ramp 0->1 over the final rampup
  seconds.  Full (1) elsewhere.
- Mix: [gated-original] + [ducked-bass] via amix=normalize=0, then numpy post-step
  for count-in windows (donor splice + even bass ramp).

Backward compat: windows without "bass_onset" treat bass_onset = end (cutoff).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from bassify.ffmpeg import ffprobe_duration, run_ffmpeg, should_skip
from bassify.paths import resolve_paths
from bassify.slice import SliceSpec

# ---------------------------------------------------------------------------
# Donor-splice constants
# ---------------------------------------------------------------------------

DONOR_LEN: float = 0.30  # seconds of prev-click audio to paste at last-click onset
FADE: float = 0.01  # seconds of linear fade in/out on the donor clip

# ---------------------------------------------------------------------------
# Pure string-builder functions — unit-testable
# ---------------------------------------------------------------------------


def build_original_gate(windows: list[dict], fade: float = 0.06) -> str:
    """Return the ffmpeg volume expression that gates the original track.

    For each window [start, cutoff]:
      - Full gain (1) from start to cutoff-fade.
      - Linear fade 1->0 from cutoff-fade to cutoff.
      - Zero after cutoff (implicit: no term contributes after cutoff).

    The terms sum to the desired gain at every point in time.
    Empty windows list returns '0' (silence).

    Parameters
    ----------
    windows:
        List of window dicts with at least 'start' and 'end' (cutoff) keys.
    fade:
        Fade-out duration in seconds ending at cutoff.

    Returns
    -------
    Volume expression string for ffmpeg's volume filter with eval=frame.
    """
    if not windows:
        return "0"

    terms: list[str] = []
    for w in windows:
        s = w["start"]
        c = w["end"]  # cutoff
        fade_start = c - fade
        # Full gain [start, cutoff-fade]
        full = f"between(t,{s:g},{fade_start:g})"
        # Linear fade from 1 to 0 over [cutoff-fade, cutoff]
        ramp = f"between(t,{fade_start:g},{c:g})*(({c:g}-t)/{fade:g})"
        terms.append(f"({full}+{ramp})")

    return "+".join(terms)


def build_bass_duck(windows: list[dict], rampup: float = 0.15) -> str:
    """Return the ffmpeg volume expression that ducks the bass in silence gaps.

    Duck = 0.0 (bass hard-muted) during each gap.

    **Count-in windows** (have ``last_click`` key): the ffmpeg expression ducks
    bass to zero across the ENTIRE span [start, bass_onset].  The numpy post-step
    in :func:`_apply_donor_splice_to_file` replaces that hard-zero with an even
    linspace(0→1) ramp from last_click to bass_onset, so there is no dead-silence
    cliff between the donor click and the bass ramp.

    **Fallback windows** (no ``last_click``): legacy behaviour — gain 0 during
    [start, bass_onset - rampup], linear ramp 0→1 over the final rampup seconds.

    Full (1) everywhere outside gaps.

    Parameters
    ----------
    windows:
        List of window dicts with 'start' and optionally 'bass_onset'.
        If 'bass_onset' is absent, falls back to 'end' (backward compat).
    rampup:
        Duration of the 0->1 ramp used for fallback (non-click) windows.

    Returns
    -------
    Volume expression string for ffmpeg's volume filter with eval=frame.
    """
    if not windows:
        return "1"

    terms: list[str] = ["1"]
    for w in windows:
        s = w["start"]
        b = w.get("bass_onset", w["end"])  # true music onset
        if "last_click" in w:
            # Count-in window: hard-zero entire gap; numpy post-step applies even ramp.
            terms.append(f"-1*between(t,{s:g},{b:g})")
        else:
            # Fallback window: zero gap, then 150ms ramp (original behaviour).
            gap_end = b - rampup
            terms.append(f"-1*between(t,{s:g},{gap_end:g})")
            terms.append(f"-1*between(t,{gap_end:g},{b:g})*(1-((t-{gap_end:g})/{rampup:g}))")

    return "".join(terms)


def build_filtergraph(orig_gate: str, bass_duck: str) -> str:
    """Assemble the full ffmpeg filter_complex string.

    Input 0 = bass (mono), input 1 = original (stereo).

    Parameters
    ----------
    orig_gate:
        Volume expression for the original gate (from build_original_gate).
    bass_duck:
        Volume expression for the bass duck (from build_bass_duck).

    Returns
    -------
    filter_complex string ready for ffmpeg -filter_complex.
    """
    return (
        f"[1:a]pan=mono|c0=0.5*c0+0.5*c1,volume='{orig_gate}':eval=frame[gap];"
        f"[0:a]volume='{bass_duck}':eval=frame[bd];"
        f"[bd][gap]amix=inputs=2:normalize=0[out]"
    )


# ---------------------------------------------------------------------------
# Legacy helpers kept for backward-compat (old combine tests import build_gate)
# ---------------------------------------------------------------------------


def build_gate(windows: list[dict]) -> str:
    """Legacy: rectangular gate (1 inside window, 0 outside). Kept for compat."""
    if not windows:
        return "0"
    return "+".join(f"between(t,{w['start']:g},{w['end']:g})" for w in windows)


# ---------------------------------------------------------------------------
# Donor-splice pure helper — unit-testable
# ---------------------------------------------------------------------------


def apply_donor_splice(
    combined: np.ndarray,
    donor: np.ndarray,
    last_click_sample: int,
    fade_samples: int,
) -> np.ndarray:
    """Add a faded donor clip at last_click_sample into combined (purely additive).

    The region before last_click_sample is untouched.  Samples after
    last_click_sample + len(donor) are also untouched.

    The donor clip is faded-in/out in a local copy (input arrays are not mutated).
    A linear fade-in of `fade_samples` is applied at the start of the donor and a
    linear fade-out of `fade_samples` at the end.

    This function is purely additive: it does NOT zero the target region first.
    The caller is responsible for ensuring the combined already has the correct
    baseline in the splice zone (e.g. the even bass ramp from apply_even_bass_ramp).

    Parameters
    ----------
    combined:
        Full audio array (1-D float32/float64).
    donor:
        Clip extracted from the original mix at the prev-click onset.
    last_click_sample:
        Sample index of the last click onset in `combined`.
    fade_samples:
        Number of samples for the linear fade in and out on the donor.

    Returns
    -------
    New array with the donor added, same dtype and length as `combined`.
    """
    out = combined.copy()
    d = donor.copy()

    # Apply fade-in
    if fade_samples > 0 and len(d) >= fade_samples:
        d[:fade_samples] *= np.linspace(0.0, 1.0, fade_samples)
    # Apply fade-out
    if fade_samples > 0 and len(d) >= fade_samples:
        d[-fade_samples:] *= np.linspace(1.0, 0.0, fade_samples)

    # Add donor (purely additive — bass ramp already applied by apply_even_bass_ramp)
    donor_len_actual = min(len(d), len(out) - last_click_sample)
    if donor_len_actual > 0:
        out[last_click_sample : last_click_sample + donor_len_actual] += d[:donor_len_actual]

    return out


def apply_even_bass_ramp(
    combined: np.ndarray,
    bass: np.ndarray,
    last_click_sample: int,
    bass_onset_sample: int,
) -> np.ndarray:
    """Add a linearly-ramped bass contribution from last_click to bass_onset.

    The ffmpeg step has hard-zeroed the bass across the entire count-in gap.
    This function adds it back with gain linspace(0, 1) over
    [last_click_sample, bass_onset_sample], so bass rises evenly from silence
    to full underneath and after the donor click.  No dead-silence cliff.

    The input arrays are not mutated; a copy of `combined` is returned.

    Parameters
    ----------
    combined:
        Current mixed audio array (1-D, same length as bass).
    bass:
        Full bass track array loaded at the same sample rate as combined.
    last_click_sample:
        Sample index of the last click onset.
    bass_onset_sample:
        Sample index of the music/bass onset (= bass_onset * sr).

    Returns
    -------
    New array with even bass ramp added, same dtype and length as `combined`.
    """
    out = combined.copy()
    span = bass_onset_sample - last_click_sample
    if span <= 0:
        return out
    # Clamp to array bounds
    i0 = max(0, last_click_sample)
    i1 = min(len(out), bass_onset_sample)
    actual_span = i1 - i0
    if actual_span <= 0:
        return out
    ramp = np.linspace(0.0, 1.0, span)[:actual_span]
    out[i0:i1] += bass[i0:i1] * ramp
    return out


# ---------------------------------------------------------------------------
# Donor-splice I/O post-step
# ---------------------------------------------------------------------------


def _apply_donor_splice_to_file(
    out_path: Path,
    original_path: Path,
    bass_path: Path,
    windows: list[dict],
) -> None:
    """For each count-in window (has prev_click + last_click), splice donor + even bass ramp.

    The ffmpeg step has hard-zeroed the bass across [start, bass_onset] for these
    windows.  This function:
      1. Adds an even linspace(0→1) bass ramp from last_click to bass_onset.
      2. Splices a 300ms copy of the original at prev_click's onset, placed at
         last_click's onset (10ms in/out fades), on top of the ramp.

    Windows without prev_click/last_click are skipped — their ffmpeg-produced
    output is unchanged.

    Parameters
    ----------
    out_path:
        Path to the combined mono WAV (pcm_s24le) produced by ffmpeg.
    original_path:
        Path to the original stereo mix (MP3 or WAV).
    bass_path:
        Path to the bass (L-R) mono WAV — needed to reconstruct the ramp.
    windows:
        List of window dicts (may contain last_click, prev_click, bass_onset).
    """
    import librosa

    splice_windows = [w for w in windows if "prev_click" in w and "last_click" in w]
    if not splice_windows:
        return

    # Load combined (PCM_24 mono)
    combined, sr = sf.read(str(out_path), dtype="float64", always_2d=False)

    # Load original resampled to combined sr (mono)
    yo = librosa.load(str(original_path), sr=sr, mono=True)[0].astype(np.float64)

    # Load bass resampled to combined sr (mono) — needed for even ramp reconstruction.
    # ffmpeg hard-zeroed bass in [start, bass_onset] for click windows; we add it
    # back with the even ramp gain in numpy.
    yb = librosa.load(str(bass_path), sr=sr, mono=True)[0].astype(np.float64)

    modified = False
    for w in splice_windows:
        prev_click = float(w["prev_click"])
        last_click = float(w["last_click"])
        bass_onset = float(w.get("bass_onset", w["end"]))

        donor_len_samples = int(DONOR_LEN * sr)
        fade_samples = int(FADE * sr)

        prev_sample = int(prev_click * sr)
        last_sample = int(last_click * sr)
        bass_onset_sample = int(bass_onset * sr)

        # Step 1: add even bass ramp [last_click, bass_onset] linspace(0,1)
        combined = apply_even_bass_ramp(combined, yb, last_sample, bass_onset_sample)

        # Step 2: extract donor from original at prev_click onset
        donor_end = min(prev_sample + donor_len_samples, len(yo))
        if donor_end <= prev_sample:
            continue
        donor = yo[prev_sample:donor_end].copy()

        if len(donor) < 2 * fade_samples:
            # Donor too short to fade cleanly — skip donor but keep the ramp
            continue

        # Step 3: splice donor at last_click (additive on top of the ramp)
        combined = apply_donor_splice(combined, donor, last_sample, fade_samples)
        modified = True

    if modified:
        sf.write(str(out_path), combined, sr, subtype="PCM_24")
        print(f"combine: donor splice + even bass ramp applied -> {out_path}")


# ---------------------------------------------------------------------------
# Public combine entrypoint
# ---------------------------------------------------------------------------


def combine_track(
    bass_path: Path,
    original_path: Path,
    windows_path: Path,
    output: Path | None = None,
    slice_spec: SliceSpec | None = None,
    cut_inputs: bool = True,
    force: bool = False,
) -> Path:
    """Mix ducked bass + gated original into a mono combined track.

    Parameters
    ----------
    bass_path:
        Path to the bass (L-R) mono audio.
    original_path:
        Path to the original stereo mix.
    windows_path:
        Path to the windows JSON file (list of window dicts).
    output:
        Output path; defaults to resolved combined path.
    slice_spec:
        Optional time-slice spec for input cutting.
    cut_inputs:
        When True, apply slice_spec to both inputs and check duration match.
    force:
        Overwrite existing output if True.

    Returns
    -------
    Path to the written combined audio file.
    """
    spec = slice_spec or SliceSpec()
    out = output or resolve_paths(original_path, slice_spec=spec).combined
    out.parent.mkdir(parents=True, exist_ok=True)
    if should_skip(out, force):
        print(f"skip (exists): {out}")
        return out

    windows = json.loads(Path(windows_path).read_text())

    orig_gate = build_original_gate(windows)
    bass_duck_expr = build_bass_duck(windows)
    fg = build_filtergraph(orig_gate, bass_duck_expr)

    if cut_inputs:
        db = ffprobe_duration(bass_path)
        do = ffprobe_duration(original_path)
        if abs(db - do) > 0.1:
            print(f"WARNING: duration mismatch bass={db:.3f}s original={do:.3f}s (mix may drift)")

    args: list[str] = []
    if cut_inputs:
        args += spec.input_args()
    args += ["-i", str(bass_path)]
    if cut_inputs:
        args += spec.input_args()
    args += [
        "-i",
        str(original_path),
        "-filter_complex",
        fg,
        "-map",
        "[out]",
        "-vn",
        "-c:a",
        "pcm_s24le",
        str(out),
    ]
    run_ffmpeg(args)

    # Post-step: for count-in windows (prev_click + last_click), apply even bass
    # ramp from last_click to bass_onset, then splice the clean donor click.
    _apply_donor_splice_to_file(out, original_path, bass_path, windows)

    return out
