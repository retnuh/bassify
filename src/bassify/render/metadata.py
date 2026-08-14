from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_NUMBER = re.compile(r"^(\d+)_")


def _filename_name(stem: str) -> str | None:
    m = _NUMBER.match(stem)
    rest = stem[m.end() :] if m else stem
    for tail in ("_bass_only", "_bass"):
        idx = rest.find(tail)
        if idx != -1:
            rest = rest[:idx]
            break
    rest = rest.strip()
    return rest.replace("_", " ") if rest else None


@dataclass(frozen=True)
class TrackMeta:
    number: str | None
    name: str | None
    artist: str | None

    def display_lines(self) -> list[str]:
        return [v for v in (self.number, self.name, self.artist) if v]


def parse_track_meta(m4a_path: Path, tags: dict[str, str]) -> TrackMeta:
    """number from filename leading digits; name/artist from tags (name falls back
    to the filename name portion). Missing fields are None; nothing raises."""
    stem = Path(m4a_path).stem
    num_m = _NUMBER.match(stem)
    number = num_m.group(1) if num_m else None
    name = tags.get("title") or _filename_name(stem)
    artist = tags.get("artist") or None
    return TrackMeta(number=number, name=name, artist=artist)
