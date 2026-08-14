from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


def _fmt(value: float) -> str:
    """Render a number compactly: 15.0 -> '15', 2.5 -> '2.5'."""
    if value == int(value):
        return str(int(value))
    return str(value)


# Regex tokens: must be followed by _, ., or end of stem string.
_DUR_TOKEN = re.compile(r"_d(\d+(?:\.\d+)?)s(?=[_.]|$)")
_START_TOKEN = re.compile(r"_s(\d+(?:\.\d+)?)s(?=[_.]|$)")


@dataclass(frozen=True)
class SliceSpec:
    """Optional test-slice window. duration/start are seconds; None means unset."""

    duration: float | None = None
    start: float | None = None

    def is_empty(self) -> bool:
        return self.duration is None and self.start is None

    def suffix(self) -> str:
        parts = []
        if self.duration is not None:
            parts.append(f"d{_fmt(self.duration)}s")
        if self.start is not None:
            parts.append(f"s{_fmt(self.start)}s")
        return "_" + "_".join(parts) if parts else ""

    def input_args(self) -> list[str]:
        """ffmpeg input-side options: -ss (start) before -t (duration)."""
        args: list[str] = []
        if self.start is not None:
            args += ["-ss", _fmt(self.start)]
        if self.duration is not None:
            args += ["-t", _fmt(self.duration)]
        return args

    @classmethod
    def from_filename(cls, path: str | Path) -> SliceSpec:
        """Parse slice tokens from a filename stem.

        Recognises ``_d<num>s`` (duration) and ``_s<num>s`` (start) tokens,
        each followed by ``_``, ``.``, or end of stem.  Returns an empty
        SliceSpec when no tokens are found.

        Examples
        --------
        >>> SliceSpec.from_filename("track_bass_d30s.wav")
        SliceSpec(duration=30.0, start=None)
        >>> SliceSpec.from_filename("track_bass_d2.5s_s15s.wav")
        SliceSpec(duration=2.5, start=15.0)
        >>> SliceSpec.from_filename("03_Turnarounds_bass.wav")
        SliceSpec(duration=None, start=None)
        """
        stem = Path(path).stem
        dur_m = _DUR_TOKEN.search(stem)
        start_m = _START_TOKEN.search(stem)
        duration = float(dur_m.group(1)) if dur_m else None
        start = float(start_m.group(1)) if start_m else None
        return cls(duration=duration, start=start)

    def load_kwargs(self) -> dict:
        """Return kwargs for librosa.load: offset + optional duration.

        Use these when loading a file that needs to be sliced to match this spec.
        The ``offset`` is always present (0.0 when start is None); ``duration``
        is only included when it is not None.
        """
        kwargs: dict = {"offset": self.start or 0.0}
        if self.duration is not None:
            kwargs["duration"] = self.duration
        return kwargs


def resolve_effective_slice(paths: list, explicit: SliceSpec | None = None) -> SliceSpec:
    """Reconcile a single slice from explicit spec + filename-encoded slices.

    Non-empty candidates must all be equal; raise ValueError on conflict.
    Returns SliceSpec() (empty) if nothing specifies a slice.

    Parameters
    ----------
    paths:
        Input file paths to check for embedded slice tokens.
    explicit:
        Optional explicit slice spec (e.g. from --duration/--start CLI flag).
    """
    cands: list[SliceSpec] = []
    if explicit is not None and not explicit.is_empty():
        cands.append(explicit)
    for p in paths:
        fs = SliceSpec.from_filename(p)
        if not fs.is_empty():
            cands.append(fs)
    if not cands:
        return SliceSpec()
    first = cands[0]
    for c in cands[1:]:
        if c != first:
            raise ValueError(f"conflicting slice specs: {first} vs {c}")
    return first


def input_needs_cut(path: str | Path, effective: SliceSpec) -> bool:
    """True if this input must be cut with ``effective``.

    Returns False when the input's filename already encodes exactly ``effective``
    (a pre-sliced intermediate can be skipped).  Returns False when both the
    filename and effective are empty (no-slice run).
    """
    return SliceSpec.from_filename(path) != effective
