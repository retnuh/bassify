"""Combine bass (ducked in gaps) + gated original (with fade) into a single mono mix.

Filtergraph strategy (duck00 variant from experiments/duck_bass.py):
- Original gate: pass [window_start, cutoff] with a linear fade-out over the last
  `fade` seconds ending at cutoff.  Nothing passes after cutoff.
- Bass duck: gain = 0 during each gap [window_start, bass_onset - rampup], then
  linear ramp 0->1 over [bass_onset - rampup, bass_onset].  Full (1) elsewhere.
- Mix: [gated-original] + [ducked-bass] via amix=normalize=0.

Backward compat: windows without "bass_onset" treat bass_onset = end (cutoff),
so old-style windows (no ducking distinction) combine without duck-vs-cutoff gap.
"""

from __future__ import annotations

import json
from pathlib import Path

from bassify.ffmpeg import ffprobe_duration, run_ffmpeg, should_skip
from bassify.paths import resolve_paths
from bassify.slice import SliceSpec

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
    For each window [start, bass_onset]:
      - Gain 0 during [start, bass_onset - rampup] (the gap).
      - Linear ramp 0->1 during [bass_onset - rampup, bass_onset].
      - Full (1) everywhere outside gaps.

    Formula: start at 1, subtract (1-duck)=1 in gap, restore in ramp.
    With duck=0: subtract 1 in gap, subtract (1-progress) in ramp.

    Parameters
    ----------
    windows:
        List of window dicts with 'start' and optionally 'bass_onset'.
        If 'bass_onset' is absent, falls back to 'end' (backward compat).
    rampup:
        Duration of the 0->1 ramp into the bass onset.

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
        gap_end = b - rampup
        # During [start, gap_end]: subtract 1 (duck to 0)
        terms.append(f"-1*between(t,{s:g},{gap_end:g})")
        # During [gap_end, bass_onset]: linear restore 0->1 (subtract remaining duck)
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
    return out
