from __future__ import annotations

from pathlib import Path

from bassify.ffmpeg import run_ffmpeg, should_skip
from bassify.paths import resolve_paths
from bassify.slice import SliceSpec, input_needs_cut, resolve_effective_slice


def build_filtergraph() -> str:
    """L = combined (input 0, mono), R = original right channel (input 1, c1).

    join maps the first input's channel to FL and the second's to FR.
    """
    return "[1:a]pan=mono|c0=c1[right];[0:a][right]join=inputs=2:channel_layout=stereo[out]"


def remix_track(
    combined_path: Path,
    original_path: Path,
    output: Path | None = None,
    slice_spec: SliceSpec | None = None,
    force: bool = False,
) -> Path:
    """Build pannable stereo: L=combined, R=original right -> remix.wav."""
    eff = resolve_effective_slice([combined_path, original_path], explicit=slice_spec)
    out = output or resolve_paths(original_path, slice_spec=eff).remix
    out.parent.mkdir(parents=True, exist_ok=True)
    if should_skip(out, force):
        print(f"skip (exists): {out}")
        return out

    # Duration-mismatch warning only when nothing is being cut (eff is empty).
    if eff.is_empty():
        from bassify.ffmpeg import ffprobe_duration
        dc = ffprobe_duration(combined_path)
        do = ffprobe_duration(original_path)
        if abs(dc - do) > 0.1:
            print(f"WARNING: duration mismatch combined={dc:.3f}s original={do:.3f}s")

    args: list[str] = []
    # Combined input: cut iff filename doesn't already encode the slice.
    if not eff.is_empty() and input_needs_cut(combined_path, eff):
        args += eff.input_args()
    args += ["-i", str(combined_path)]
    # Original input: cut iff filename doesn't already encode the slice.
    if not eff.is_empty() and input_needs_cut(original_path, eff):
        args += eff.input_args()
    args += [
        "-i",
        str(original_path),
        "-filter_complex",
        build_filtergraph(),
        "-map",
        "[out]",
        "-vn",
        "-c:a",
        "pcm_s24le",
        str(out),
    ]
    run_ffmpeg(args)
    return out
