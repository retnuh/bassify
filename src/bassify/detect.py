from __future__ import annotations

import json
import re
from pathlib import Path

from bassify.ffmpeg import ffprobe_duration, run_ffmpeg_capture, should_skip
from bassify.paths import resolve_paths
from bassify.slice import SliceSpec

_START = re.compile(r"silence_start:\s*([0-9.]+)")
_END = re.compile(r"silence_end:\s*([0-9.]+)")


def parse_silences(
    stderr: str,
    duration: float,
    pad_start: float = 0.0,
    pad_end: float = -0.05,
) -> list[dict[str, float]]:
    """Pair silence_start/end lines into padded, clamped windows.

    An unpaired trailing silence_start is closed at `duration`.

    pad_start: seconds subtracted from the raw silence_start (positive expands outward,
               negative pulls the gate start later into the silence).
    pad_end:   seconds added to the raw silence_end (positive expands outward,
               negative pulls the gate end earlier, before bass onset).
    Windows that become zero-length or inverted after padding are dropped.
    """
    windows: list[dict[str, float]] = []
    pending: float | None = None
    for line in stderr.splitlines():
        m_start = _START.search(line)
        if m_start:
            pending = float(m_start.group(1))
            continue
        m_end = _END.search(line)
        if m_end and pending is not None:
            windows.append({"start": pending, "end": float(m_end.group(1))})
            pending = None
    if pending is not None:
        windows.append({"start": pending, "end": duration})

    clamped: list[dict[str, float]] = []
    for w in windows:
        start = max(0.0, w["start"] - pad_start)
        end = min(duration, w["end"] + pad_end)
        if start >= end:
            continue  # degenerate window — drop it
        clamped.append({"start": start, "end": end})

    # Merge overlapping or touching windows (can occur after padding).
    if not clamped:
        return []
    merged: list[dict[str, float]] = [clamped[0]]
    for nxt in clamped[1:]:
        cur = merged[-1]
        if nxt["start"] <= cur["end"]:
            # Overlapping or touching — extend current window if needed.
            merged[-1] = {"start": cur["start"], "end": max(cur["end"], nxt["end"])}
        else:
            merged.append(nxt)
    return merged


def detect_windows(
    bass_path: Path,
    original_for_naming: Path | None = None,
    output: Path | None = None,
    threshold: float = -40.0,
    min_gap: float = 1.0,
    pad_start: float = 0.0,
    pad_end: float = -0.05,
    slice_spec: SliceSpec | None = None,
    cut_inputs: bool = True,
    force: bool = False,
) -> Path:
    """Run silencedetect on the bass track, write windows JSON. Returns output path.

    `original_for_naming` lets `run` place the JSON in the track dir keyed off the
    original input name; when None the bass_path stem is used for naming.
    """
    spec = slice_spec or SliceSpec()
    if output is not None:
        out = output
    elif original_for_naming is not None:
        out = resolve_paths(original_for_naming, slice_spec=spec).windows
    else:
        out = bass_path.with_name(bass_path.stem + "_silence_windows.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    if should_skip(out, force):
        print(f"skip (exists): {out}")
        return out

    args: list[str] = []
    if cut_inputs:
        args += spec.input_args()
    args += [
        "-i",
        str(bass_path),
        "-af",
        f"silencedetect=noise={threshold:g}dB:d={min_gap:g}",
        "-f",
        "null",
        "-",
    ]
    stderr = run_ffmpeg_capture(args)
    duration = ffprobe_duration(bass_path)
    windows = parse_silences(stderr, duration=duration, pad_start=pad_start, pad_end=pad_end)
    out.write_text(json.dumps(windows, indent=2))
    print(f"wrote {len(windows)} windows -> {out}")
    return out
