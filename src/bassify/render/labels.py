from __future__ import annotations

import math
from importlib.resources import files
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

AXIS_H = 48  # fixed strip height; passed to showcqt as axis_h so the PNG maps 1:1

# Half-semitone margin added to each side of the CQT frame. Keeps the endpoint
# notes (e.g. C2/C4) off the very screen edges where their labels clip. Applied
# identically to showcqt (filtergraph) and to note_x here so the two stay aligned.
FRAME_PAD = 2 ** (0.5 / 12)


def padded_frame(basefreq: float, endfreq: float) -> tuple[float, float]:
    """The CQT display frame: the musical range widened a half-semitone each side."""
    return basefreq / FRAME_PAD, endfreq * FRAME_PAD


def _midi(freq: float) -> int:
    return round(12 * math.log2(freq / 440.0) + 69)


_BLUES_BIG = frozenset({0, 3, 5, 7, 10})  # 1, b3, 4, 5, b7
_FLAT5 = frozenset({6})  # b5 — the blue note; blue, and big (it IS in the scale)
_MID = frozenset({2, 4, 9})  # 2, 3, 6 — major-blues additions: in the scale, sort of

# Size encodes how far in the scale a note is; the accidental and the root's
# octave digit are drawn smaller than the letter so a 3-glyph root label still
# fits its cell (monospace, so swapping '#' for U+266F saves no width by itself).
_SIZE = {"big": 30, "mid": 20, "small": 16}
_ACC_SIZE = {"big": 22, "mid": 18, "small": 16}
_YOFF = {"big": 6, "mid": 12, "small": 14}

_SHARP = "♯"

# Hue is reserved for the two notes worth naming; everything else is a
# brightness ramp, so size and brightness always agree.
_GOLD = (255, 215, 0, 255)  # root
_BLUE = (60, 140, 235, 255)  # b5, the blue note
_RAMP = {
    "big": (255, 255, 255, 255),
    "mid": (205, 205, 205, 235),
    "small": (150, 150, 150, 215),
}
_OUTLINE = (0, 0, 0, 255)


def note_name(pitch_class: int) -> str:
    """Sharp-spelled note name for a pitch class 0-11 (0=C)."""
    return _NAMES[pitch_class]


def note_x(freq: float, basefreq: float, endfreq: float, width: int) -> float:
    """Screen-x of a frequency in a log2 CQT axis. Matches showcqt's mapping."""
    return width * math.log2(freq / basefreq) / math.log2(endfreq / basefreq)


def note_tier(pitch_class: int, root_pc: int | None) -> str:
    """Blues-scale tier of a pitch class relative to the root.
    root_pc None -> every note 'big' (neutral labels).

    'big' is the blues hexatonic scale, b5 included -- the b5 is set apart by
    colour, not by size. 'mid' is the 2/3/6 major-blues additions, which sit
    between in-scale and outside. Everything else is 'small'.
    """
    if root_pc is None:
        return "big"
    off = (pitch_class - root_pc) % 12
    if off in _BLUES_BIG or off in _FLAT5:
        return "big"
    if off in _MID:
        return "mid"
    return "small"


def note_color(pitch_class: int, root_pc: int | None) -> tuple[int, int, int, int]:
    """Label colour: gold root, blue b5, otherwise a brightness ramp by tier."""
    tier = note_tier(pitch_class, root_pc)
    if root_pc is None:
        return _RAMP[tier]
    off = (pitch_class - root_pc) % 12
    if off == 0:
        return _GOLD
    if off in _FLAT5:
        return _BLUE
    return _RAMP[tier]


def _midi_freq(midi: int) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _font(font_path: str | None, size: int) -> ImageFont.FreeTypeFont:
    if font_path is None:
        font_path = str(files("bassify.render.fonts") / "DejaVuSansMono.ttf")
    return ImageFont.truetype(font_path, size)


def _draw_glyph(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: float,
    y: float,
    font: ImageFont.FreeTypeFont,
    color: tuple[int, int, int, int],
    stroke: int,
) -> float:
    """Draw one glyph and return its advance, so callers can chain along x."""
    draw.text((x, y), text, font=font, fill=color, stroke_width=stroke, stroke_fill=_OUTLINE)
    return font.getlength(text)


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
    acc_fonts = {t: _font(font_path, s) for t, s in _ACC_SIZE.items()}
    img = Image.new("RGBA", (width, axis_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad_lo, pad_hi = padded_frame(basefreq, endfreq)
    # Label the musical range by MIDI bounds (avoids float rounding dropping the
    # bottom endpoint).
    lo_midi, hi_midi = _midi(basefreq), _midi(endfreq)
    # Every semitone is the same width in a log2 axis, so one cell width serves
    # for centring every label.
    cell = note_x(_midi_freq(lo_midi + 1), pad_lo, pad_hi, width) - note_x(
        _midi_freq(lo_midi), pad_lo, pad_hi, width
    )
    # Inclusive of the top note so its gridline closes the last cell, but its
    # LABEL is skipped below: the octave-boundary C sits at the extreme right
    # and its text would clip off-screen. Line and label are separate concerns.
    for midi in range(lo_midi, hi_midi + 1):
        f = _midi_freq(midi)
        x = note_x(f, pad_lo, pad_hi, width)
        pc = midi % 12
        tier = note_tier(pc, root_pc)
        is_root = root_pc is not None and pc == root_pc
        color = note_color(pc, root_pc)
        draw.line([(x, 0), (x, axis_h)], fill=(0, 0, 0, 180), width=3)
        draw.line([(x, 0), (x, axis_h)], fill=color, width=1)
        if midi == hi_midi:
            continue  # closing gridline only — its label would clip off-screen

        name = _NAMES[pc]
        letter, accidental = name[0], _SHARP if len(name) > 1 else ""
        octave = str(midi // 12 - 1) if is_root else ""
        font, acc_font = fonts[tier], acc_fonts[tier]

        span = (
            font.getlength(letter)
            + (acc_font.getlength(accidental) if accidental else 0.0)
            + (acc_font.getlength(octave) if octave else 0.0)
        )
        cx, y = x + (cell - span) / 2, _YOFF[tier]

        cx += _draw_glyph(draw, letter, cx, y, font, color, 3)
        if accidental:
            cx += _draw_glyph(draw, accidental, cx, y + 2, acc_font, color, 2)
        if octave:
            _draw_glyph(draw, octave, cx, y + 6, acc_font, color, 2)
    img.save(out_path)
    return out_path
