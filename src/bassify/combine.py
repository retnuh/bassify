from __future__ import annotations

import json
from pathlib import Path

from bassify.ffmpeg import ffprobe_duration, run_ffmpeg, should_skip
from bassify.paths import resolve_paths
from bassify.slice import SliceSpec


def build_gate(windows: list[dict[str, float]]) -> str:
    """Sum of between() terms: 1 inside any window, 0 outside. Empty -> '0'."""
    if not windows:
        return "0"
    return "+".join(f"between(t,{w['start']:g},{w['end']:g})" for w in windows)


def build_filtergraph(gate: str) -> str:
    """Gate the original (downmixed to mono) during windows, mix onto mono bass.

    Input 0 = bass (mono), input 1 = original (stereo). eval=frame is required so
    the gate re-evaluates per frame; normalize=0 stops amix halving the inputs.
    """
    return (
        f"[1:a]pan=mono|c0=0.5*c0+0.5*c1,volume='{gate}':eval=frame[gap];"
        f"[0:a][gap]amix=inputs=2:normalize=0[out]"
    )


def combine_track(
    bass_path: Path,
    original_path: Path,
    windows_path: Path,
    output: Path | None = None,
    slice_spec: SliceSpec | None = None,
    cut_inputs: bool = True,
    force: bool = False,
) -> Path:
    """Mix gated speech/count-ins onto the mono bass -> combined.wav."""
    spec = slice_spec or SliceSpec()
    out = output or resolve_paths(original_path, slice_spec=spec).combined
    out.parent.mkdir(parents=True, exist_ok=True)
    if should_skip(out, force):
        print(f"skip (exists): {out}")
        return out

    windows = json.loads(Path(windows_path).read_text())
    gate = build_gate(windows)
    fg = build_filtergraph(gate)

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
