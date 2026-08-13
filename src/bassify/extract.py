from __future__ import annotations

from pathlib import Path

from bassify.ffmpeg import run_ffmpeg, should_skip
from bassify.paths import resolve_paths
from bassify.slice import SliceSpec

DEFAULT_LOWPASS = 800.0


def build_filter(lowpass: float | None) -> str:
    f = "pan=mono|c0=c0-c1"
    if lowpass is not None:
        f += f",lowpass=f={lowpass:g}"
    return f


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
