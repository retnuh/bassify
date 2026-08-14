from __future__ import annotations

from pathlib import Path

from bassify.ffmpeg import ffprobe_duration, run_ffmpeg, should_skip
from bassify.paths import resolve_paths
from bassify.slice import SliceSpec


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
    cut_inputs: bool = True,
    force: bool = False,
) -> Path:
    """Build pannable stereo: L=combined, R=original right -> remix.wav."""
    spec = slice_spec or SliceSpec()
    out = output or resolve_paths(original_path, slice_spec=spec).remix
    out.parent.mkdir(parents=True, exist_ok=True)
    if should_skip(out, force):
        print(f"skip (exists): {out}")
        return out

    if cut_inputs:
        dc = ffprobe_duration(combined_path)
        do = ffprobe_duration(original_path)
        if abs(dc - do) > 0.1:
            print(f"WARNING: duration mismatch combined={dc:.3f}s original={do:.3f}s")

    args: list[str] = []
    if cut_inputs:
        args += spec.input_args()
    args += ["-i", str(combined_path)]
    if cut_inputs:
        args += spec.input_args()
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
