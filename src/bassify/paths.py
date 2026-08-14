from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bassify.slice import SliceSpec


@dataclass(frozen=True)
class Paths:
    track_dir: Path
    bass: Path
    windows: Path
    bass_only: Path
    remix: Path
    bass_only_m4a: Path
    remix_m4a: Path


def resolve_paths(
    input_path: Path,
    out_root: Path = Path("out"),
    slice_spec: SliceSpec | None = None,
) -> Paths:
    """Build every artifact path for one input track.

    collection = immediate parent dir name; track = input stem.
    The slice suffix (if any) is inserted after the artifact type, before ext.
    """
    input_path = Path(input_path)
    spec = slice_spec or SliceSpec()
    sfx = spec.suffix()
    collection = input_path.parent.name
    track = input_path.stem
    track_dir = Path(out_root) / collection / track

    def name(kind: str, ext: str) -> Path:
        return track_dir / f"{track}_{kind}{sfx}.{ext}"

    return Paths(
        track_dir=track_dir,
        bass=name("bass", "wav"),
        windows=name("silence_windows", "json"),
        bass_only=name("bass_only", "wav"),
        remix=name("remix", "wav"),
        bass_only_m4a=name("bass_only", "m4a"),
        remix_m4a=name("remix", "m4a"),
    )
