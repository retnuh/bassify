from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf


def compute_residual_db(
    bass_path: Path, windows_path: Path, exclude_count_in: bool = False
) -> float:
    """Residual (non-bass leak) energy during bass-silent windows, in dB
    relative to the track's overall bass energy outside those windows.

    Lower (more negative) is better -- less audible leak during gaps.

    exclude_count_in: when True, drop windows that carry a "last_click" key
    (detect.py's count-in refinement -- a click-in after a stop, which on
    instructional tracks often brackets spoken narration between examples
    rather than a genuine mid-song bass rest). Scoring only the remaining
    plain silencedetect gaps avoids conflating voice bleed with guitar
    bleed. Default False preserves the original whole-windows-file scoring.
    """
    y, sr = sf.read(str(bass_path), dtype="float64", always_2d=False)
    windows = json.loads(Path(windows_path).read_text())
    if exclude_count_in:
        windows = [w for w in windows if "last_click" not in w]

    silent_mask = np.zeros(len(y), dtype=bool)
    for w in windows:
        start_sample = int(w["start"] * sr)
        end_sample = min(int(w["end"] * sr), len(y))
        silent_mask[start_sample:end_sample] = True

    residual_rms = np.sqrt(np.mean(y[silent_mask] ** 2)) if silent_mask.any() else 0.0
    bass_rms = np.sqrt(np.mean(y[~silent_mask] ** 2)) if (~silent_mask).any() else 1e-12

    ratio = residual_rms / max(bass_rms, 1e-12)
    return float(20 * np.log10(max(ratio, 1e-12)))


def scan_collection(
    collection_dir: Path, exclude_count_in: bool = False
) -> list[tuple[str, float, float | None]]:
    """For each track directory under collection_dir, compute (name, before_db,
    after_db) using the dirty bass.wav and (if present) bass_clean.wav, both
    scored against the same silence-windows JSON.

    exclude_count_in: passed through to compute_residual_db -- see its
    docstring.
    """
    rows: list[tuple[str, float, float | None]] = []
    for track_dir in sorted(Path(collection_dir).iterdir()):
        if not track_dir.is_dir():
            continue
        windows_path = next(track_dir.glob("*_silence_windows*.json"), None)
        bass_path = next(
            (p for p in track_dir.glob("*_bass.wav") if "_bass_clean" not in p.name), None
        )
        bass_clean_path = next(track_dir.glob("*_bass_clean.wav"), None)
        if windows_path is None or bass_path is None:
            continue
        before = compute_residual_db(bass_path, windows_path, exclude_count_in=exclude_count_in)
        after = (
            compute_residual_db(bass_clean_path, windows_path, exclude_count_in=exclude_count_in)
            if bass_clean_path
            else None
        )
        rows.append((track_dir.name, before, after))
    return rows


def print_report(rows: list[tuple[str, float, float | None]]) -> None:
    print(f"{'track':<45} {'before (dB)':>12} {'after (dB)':>12} {'delta (dB)':>12}")
    for name, before, after in rows:
        if after is None:
            after_str = "n/a"
            delta_str = "n/a"
        else:
            after_str = f"{after:.1f}"
            delta_str = f"{after - before:+.1f}"
        print(f"{name:<45} {before:>12.1f} {after_str:>12} {delta_str:>12}")
