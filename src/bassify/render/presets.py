from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class RenderPreset:
    name: str
    width: int
    height: int
    fps: int
    count: int
    labels: bool
    waveform: bool
    overlays: bool
    x264_preset: str
    crf: int
    basefreq: float
    endfreq: float
    still: bool


_C2, _C4 = 65.41, 261.63  # default CQT bass framing

PRESETS: dict[str, RenderPreset] = {
    "draft": RenderPreset("draft", 1280, 720, 30, 2, False, False, False,
                          "fast", 20, _C2, _C4, False),
    "final": RenderPreset("final", 1280, 720, 30, 4, True, True, True,
                          "slow", 20, _C2, _C4, False),
    "still": RenderPreset("still", 1280, 720, 2, 0, False, False, False,
                          "ultrafast", 20, _C2, _C4, True),
}


def apply_overrides(
    preset: RenderPreset,
    *,
    res: str | None = None,
    fps: int | None = None,
    count: int | None = None,
    crf: int | None = None,
    freq_range: tuple[float, float] | None = None,
    no_waveform: bool = False,
    no_labels: bool = False,
) -> RenderPreset:
    changes: dict = {}
    if res is not None:
        w, h = res.lower().split("x")
        changes["width"], changes["height"] = int(w), int(h)
    if fps is not None:
        changes["fps"] = fps
    if count is not None:
        changes["count"] = count
    if crf is not None:
        changes["crf"] = crf
    if freq_range is not None:
        changes["basefreq"], changes["endfreq"] = freq_range
    if no_waveform:
        changes["waveform"] = False
    if no_labels:
        changes["labels"] = False
    return replace(preset, **changes)
