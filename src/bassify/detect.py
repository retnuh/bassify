from __future__ import annotations

import json
import re
from pathlib import Path

from bassify.countin import refine_window_full
from bassify.ffmpeg import ffprobe_duration, run_ffmpeg_capture, should_skip
from bassify.slice import SliceSpec, input_needs_cut, resolve_effective_slice

_START = re.compile(r"silence_start:\s*([0-9.]+)")
_END = re.compile(r"silence_end:\s*([0-9.]+)")


def parse_silences(
    stderr: str,
    duration: float,
    pad_start: float = 0.0,
    pad_end: float = -0.05,
    min_riff: float = 0.5,
) -> list[dict[str, float]]:
    """Pair silence_start/end lines into padded, clamped windows.

    An unpaired trailing silence_start is closed at `duration`.

    pad_start: seconds subtracted from the raw silence_start (positive expands outward,
               negative pulls the gate start later into the silence).
    pad_end:   seconds added to the raw silence_end (positive expands outward,
               negative pulls the gate end earlier, before bass onset).
    min_riff:  merge adjacent raw windows whose non-silent gap is shorter than this many
               seconds (removes gaps split by brief noise-floor blips). Set to 0 to disable.
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

    # Merge windows split by sub-threshold noise blips (adjacent gaps < min_riff apart).
    # Operates on raw windows before padding so artifact-sized gaps vanish cleanly.
    if min_riff > 0 and len(windows) > 1:
        riff_merged: list[dict[str, float]] = [windows[0]]
        for nxt in windows[1:]:
            cur = riff_merged[-1]
            if nxt["start"] - cur["end"] < min_riff:
                riff_merged[-1] = {"start": cur["start"], "end": nxt["end"]}
            else:
                riff_merged.append(nxt)
        windows = riff_merged

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


def drop_trailing_window(
    windows: list[dict],
    duration: float,
    epsilon: float = 1.0,
) -> list[dict]:
    """Drop the last window if its silence reaches the track end.

    Uses 'bass_onset' (the pre-refinement silence end) if present, else 'end'.
    Rationale: after count-in refinement 'end' is the click cutoff, not the true
    silence boundary; 'bass_onset' is the value that tells us whether the silence
    ran all the way to the end of the track.

    Only the last window is ever considered; earlier windows are untouched.
    Returns a new list (original is not mutated).
    """
    if not windows:
        return windows
    last = windows[-1]
    trailing_end = last.get("bass_onset", last["end"])
    if trailing_end >= duration - epsilon:
        return windows[:-1]
    return windows


def detect_windows(
    bass_path: Path,
    output: Path | None = None,
    threshold: float = -40.0,
    min_gap: float = 1.0,
    pad_start: float = 0.0,
    pad_end: float = -0.05,
    min_riff: float = 0.5,
    slice_spec: SliceSpec | None = None,
    force: bool = False,
    original_path: Path | None = None,
    drop_trailing: bool = True,
) -> Path:
    """Run silencedetect on the bass track, write windows JSON. Returns output path.

    ``output`` sets the JSON path explicitly; when None the bass_path stem is used
    for naming.

    ``original_path`` enables count-in click cutoff refinement: each window's end
    is refined to sit just after the last count-in click, before the downbeat.
    When None (default), behaviour is unchanged.

    ``drop_trailing``: when True (default), the final window is dropped if its silence
    end reaches the track end (within 1.0 s). This removes the typical end-of-track
    fade/silence that is not a count-in gap.

    Slice reconciliation: resolves the effective slice from ``slice_spec`` and the
    filename-embedded slice on ``bass_path`` (and ``original_path`` when given).
    Only cuts an input via ffmpeg if it is not already pre-sliced on disk.
    """
    paths_to_check = [bass_path]
    if original_path is not None:
        paths_to_check.append(original_path)
    eff = resolve_effective_slice(paths_to_check, explicit=slice_spec)

    if output is not None:
        out = output
    else:
        out = bass_path.with_name(bass_path.stem + "_silence_windows.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    if should_skip(out, force):
        print(f"skip (exists): {out}")
        return out

    args: list[str] = []
    bass_needs_cut = input_needs_cut(bass_path, eff)
    if bass_needs_cut and not eff.is_empty():
        args += eff.input_args()
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

    # Duration for parse_silences clamping:
    # - If bass is pre-sliced on disk (input_needs_cut=False), ffprobe returns slice duration.
    # - If we cut bass via ffmpeg (input_needs_cut=True) and eff.duration is set, use eff.duration.
    # - Otherwise, ffprobe the file.
    if bass_needs_cut and not eff.is_empty() and eff.duration is not None:
        duration = eff.duration
    else:
        duration = ffprobe_duration(bass_path)

    windows = parse_silences(
        stderr, duration=duration, pad_start=pad_start, pad_end=pad_end, min_riff=min_riff
    )

    # Optionally refine each window's end via count-in guitar-onset cutoff detection.
    # Refined windows store both:
    #   "end"        = the guitar-onset cutoff (gate closes here)
    #   "bass_onset" = the original silence-end / true music onset (duck ramp target)
    if original_path is not None:
        refined: list[dict] = []
        for w in windows:
            bass_onset = w["end"]  # raw silence end = true music onset
            info = refine_window_full(
                bass_path, original_path, w["start"], bass_onset, orig_slice=eff
            )
            entry: dict = {
                "start": w["start"],
                "end": info["cutoff"],
                "bass_onset": bass_onset,
            }
            if info["last_click"] is not None and info["prev_click"] is not None:
                entry["last_click"] = info["last_click"]
                entry["prev_click"] = info["prev_click"]
            refined.append(entry)
        windows = refined

    if drop_trailing:
        windows = drop_trailing_window(windows, duration)

    out.write_text(json.dumps(windows, indent=2))
    print(f"wrote {len(windows)} windows -> {out}")
    return out
