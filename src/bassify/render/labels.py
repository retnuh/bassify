from __future__ import annotations

import math
from importlib.resources import files
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

AXIS_H = 48  # fixed strip height; passed to showcqt as axis_h so the PNG maps 1:1

_BLUES_BIG = frozenset({0, 3, 5, 7, 10})  # 1, b3, 4, 5, b7
_FLAT5 = frozenset({6})                    # b5 — blue note (red, medium)

_SIZE = {"big": 30, "med": 24, "small": 16}
_YOFF = {"big": 6, "med": 10, "small": 14}
_GOLD = (255, 215, 0, 255)
_WHITE = (255, 255, 255, 255)
_RED = (255, 60, 60, 255)
_GREY = (150, 150, 150, 220)
_OUTLINE = (0, 0, 0, 255)


def note_x(freq: float, basefreq: float, endfreq: float, width: int) -> float:
    """Screen-x of a frequency in a log2 CQT axis. Matches showcqt's mapping."""
    return width * math.log2(freq / basefreq) / math.log2(endfreq / basefreq)


def note_tier(pitch_class: int, root_pc: int | None) -> str:
    """Blues-scale tier of a pitch class relative to the root.
    root_pc None -> every note 'big' (neutral labels)."""
    if root_pc is None:
        return "big"
    off = (pitch_class - root_pc) % 12
    if off in _FLAT5:
        return "med"
    if off in _BLUES_BIG:
        return "big"
    return "small"


def _midi_freq(midi: int) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _font(font_path: str | None, size: int) -> ImageFont.FreeTypeFont:
    if font_path is None:
        font_path = str(files("bassify.render.fonts") / "DejaVuSansMono.ttf")
    return ImageFont.truetype(font_path, size)


def build_axis_strip(
    out_path: Path,
    width: int,
    basefreq: float,
    endfreq: float,
    root_pc: int | None,
    font_path: str | None = None,
    axis_h: int = AXIS_H,
) -> Path:
    """Write a width×axis_h RGBA axisfile PNG with key-aware tiered note labels.
    Alpha-0 background; black stroke outline for contrast on the bright CQT."""
    out_path = Path(out_path)
    fonts = {t: _font(font_path, s) for t, s in _SIZE.items()}
    img = Image.new("RGBA", (width, axis_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for midi in range(12, 108):  # C0..B7
        f = _midi_freq(midi)
        if f < basefreq or f > endfreq:
            continue
        x = note_x(f, basefreq, endfreq, width)
        pc = midi % 12
        tier = note_tier(pc, root_pc)
        is_root = root_pc is not None and pc == root_pc
        if is_root:
            color = _GOLD
        elif tier == "med":
            color = _RED
        elif tier == "big":
            color = _WHITE
        else:
            color = _GREY
        draw.line([(x, 0), (x, axis_h)], fill=(0, 0, 0, 180), width=3)
        draw.line([(x, 0), (x, axis_h)], fill=color, width=1)
        label = f"{_NAMES[pc]}{midi // 12 - 1}" if is_root else _NAMES[pc]
        draw.text((x + 3, _YOFF[tier]), label, font=fonts[tier], fill=color,
                  stroke_width=3, stroke_fill=_OUTLINE)
    img.save(out_path)
    return out_path
