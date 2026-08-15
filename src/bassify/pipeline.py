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

    The ffmpeg time-cut is applied at extract (producing a pre-sliced bass WAV).
    Downstream stages (detect/combine/remix) reconcile the effective slice from
    filename tokens, so they correctly apply the slice to the full original without
    re-cutting the already-sliced bass intermediate.
    """
    input_path = Path(input_path)
    paths = resolve_paths(input_path, slice_spec=slice_spec)
    paths.track_dir.mkdir(parents=True, exist_ok=True)

    bass = extract_bass(
        input_path,
        output=paths.bass,
        output_clean=paths.bass_clean,
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
        force=force,
        original_path=input_path,
    )
    bass_only = combine_track(
        paths.bass_clean,
        input_path,
        windows,
        output=paths.bass_only,
        slice_spec=slice_spec,
        force=force,
    )
    remixed = remix_track(
        bass_only,
        input_path,
        output=paths.remix,
        slice_spec=slice_spec,
        force=force,
    )
    encode_track(
        bass_only, input_path, output=paths.bass_only_m4a, force=force, title_suffix="(Bass Only)"
    )
    encode_track(remixed, input_path, output=paths.remix_m4a, force=force)
    print(f"done: {paths.track_dir}")


_SOURCE_GLOBS = ("*.mp3", "*.m4a", "*.flac", "*.wav", "*.aac", "*.ogg")


def _collect_tracks(input_dir: Path) -> list[Path]:
    """Sorted, de-duped source audio files directly in input_dir (non-recursive)."""
    tracks: list[Path] = []
    for pattern in _SOURCE_GLOBS:
        tracks.extend(input_dir.glob(pattern))
    return sorted(set(tracks))


def extract_batch(
    input_dir: Path,
    lowpass: float | None = DEFAULT_LOWPASS,
    slice_spec: SliceSpec | None = None,
    force: bool = False,
) -> None:
    """Run only the extract stage on every source track in input_dir (non-recursive).

    Handy for regenerating bass.wav files after `just clean` removed the WAV
    intermediates. Outputs go to out/<collection>/<track>/. A failure on one
    track is printed and skipped.
    """
    input_dir = Path(input_dir)
    tracks = _collect_tracks(input_dir)
    if not tracks:
        print(f"batch: no audio files found in {input_dir}")
        return

    total = len(tracks)
    ok = 0
    failed = 0
    for i, track in enumerate(tracks, 1):
        print(f"[{i}/{total}] extracting {track.name}")
        try:
            extract_bass(track, lowpass=lowpass, slice_spec=slice_spec, force=force)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR extracting {track.name}: {exc}")
            failed += 1

    print(f"extract batch done: {ok} ok, {failed} failed")


def run_batch(
    input_dir: Path,
    lowpass: float | None = DEFAULT_LOWPASS,
    threshold: float = -40.0,
    min_gap: float = 1.0,
    slice_spec: SliceSpec | None = None,
    force: bool = False,
) -> None:
    """Process every source audio track directly in input_dir (non-recursive).

    Source extensions: .mp3, .m4a, .flac, .wav, .aac, .ogg.
    Outputs go to out/<collection>/<track>/ so there is no self-collision with the input dir.
    Tracks are processed in sorted order; a failure on one track is printed and skipped.
    """
    input_dir = Path(input_dir)
    tracks = _collect_tracks(input_dir)

    if not tracks:
        print(f"batch: no audio files found in {input_dir}")
        return

    total = len(tracks)
    ok = 0
    failed = 0
    for i, track in enumerate(tracks, 1):
        print(f"[{i}/{total}] processing {track.name}")
        try:
            run_pipeline(
                track,
                lowpass=lowpass,
                threshold=threshold,
                min_gap=min_gap,
                slice_spec=slice_spec,
                force=force,
            )
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR processing {track.name}: {exc}")
            failed += 1

    print(f"batch done: {ok} ok, {failed} failed")
