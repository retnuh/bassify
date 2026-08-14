from __future__ import annotations

from pathlib import Path

from bassify.combine import combine_track
from bassify.detect import detect_windows
from bassify.encode import encode_track
from bassify.extract import DEFAULT_LOWPASS, extract_bass
from bassify.paths import resolve_paths
from bassify.remix import remix_track
from bassify.slice import SliceSpec


def run_pipeline(
    input_path: Path,
    lowpass: float | None = DEFAULT_LOWPASS,
    threshold: float = -40.0,
    min_gap: float = 1.0,
    slice_spec: SliceSpec | None = None,
    force: bool = False,
) -> None:
    """extract -> detect -> combine -> remix -> encode x2.

    The ffmpeg time-cut is applied ONLY at extract; downstream stages read the
    already-sliced WAVs (cut_inputs=False) but keep the slice suffix in names.
    """
    input_path = Path(input_path)
    paths = resolve_paths(input_path, slice_spec=slice_spec)
    paths.track_dir.mkdir(parents=True, exist_ok=True)

    bass = extract_bass(
        input_path,
        output=paths.bass,
        lowpass=lowpass,
        slice_spec=slice_spec,
        cut_inputs=True,
        force=force,
    )
    windows = detect_windows(
        bass,
        output=paths.windows,
        threshold=threshold,
        min_gap=min_gap,
        slice_spec=slice_spec,
        cut_inputs=False,
        force=force,
        original_path=input_path,
    )
    combined = combine_track(
        bass,
        input_path,
        windows,
        output=paths.combined,
        slice_spec=slice_spec,
        cut_inputs=False,
        force=force,
    )
    remixed = remix_track(
        combined,
        input_path,
        output=paths.remix,
        slice_spec=slice_spec,
        cut_inputs=False,
        force=force,
    )
    encode_track(combined, input_path, output=paths.combined_m4a, force=force)
    encode_track(remixed, input_path, output=paths.remix_m4a, force=force)
    print(f"done: {paths.track_dir}")
