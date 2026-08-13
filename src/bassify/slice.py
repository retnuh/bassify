from __future__ import annotations

from dataclasses import dataclass


def _fmt(value: float) -> str:
    """Render a number compactly: 15.0 -> '15', 2.5 -> '2.5'."""
    if value == int(value):
        return str(int(value))
    return str(value)


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
