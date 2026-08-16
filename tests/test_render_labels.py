from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

from bassify.render.labels import (
    AXIS_H,
    FRAME_PAD,
    build_axis_strip,
    note_tier,
    note_x,
    padded_frame,
)

BASE, END, W = 65.41, 261.63, 1280  # C2..C4


def test_note_x_endpoints():
    assert note_x(BASE, BASE, END, W) == 0.0
    assert note_x(END, BASE, END, W) == W


def test_frame_pad_is_half_semitone():
    assert abs(FRAME_PAD - 2 ** (0.5 / 12)) < 1e-12


def test_padded_frame_widens_both_sides():
    lo, hi = padded_frame(BASE, END)
    assert lo < BASE and hi > END
    assert abs(lo - BASE / 2 ** (0.5 / 12)) < 1e-9
    assert abs(hi - END * 2 ** (0.5 / 12)) < 1e-9


def test_bottom_c_labelled_top_c_dropped(tmp_path: Path):
    """C2 (65.4064 < basefreq 65.41) must still render; the top C4 must not."""
    from PIL import Image

    out = tmp_path / "axis.png"
    build_axis_strip(out, width=W, basefreq=BASE, endfreq=END, root_pc=0)
    img = Image.open(out).convert("RGBA")
    # bottom C2 is near the far left; a labelled pixel column (non-zero alpha)
    # must exist in the first ~2% of the width
    left = img.crop((0, 0, int(W * 0.02), AXIS_H))
    assert left.getchannel("A").getextrema()[1] > 0
    # the top C4 label previously clipped at x=1280; the rightmost 1px column
    # must be empty now (no note drawn at the very edge)
    right = img.crop((W - 1, 0, W, AXIS_H))
    assert right.getchannel("A").getextrema()[1] == 0


def test_note_x_one_octave():
    assert abs(note_x(2 * BASE, BASE, END, W) - W / math.log2(END / BASE)) < 1e-6


def test_axis_h_is_48():
    assert AXIS_H == 48


def test_note_tier_with_root_E():
    E = 4
    assert note_tier(4, E) == "big"
    assert note_tier(7, E) == "big"  # G = b3
    assert note_tier(9, E) == "big"  # A = 4
    assert note_tier(11, E) == "big"  # B = 5
    assert note_tier(2, E) == "big"  # D = b7
    assert note_tier(10, E) == "big"  # A# = b5 -- in the scale; set apart by colour
    assert note_tier(6, E) == "mid"  # F# = 2
    assert note_tier(8, E) == "mid"  # G# = 3
    assert note_tier(1, E) == "mid"  # C# = 6 (major 6th)
    assert note_tier(5, E) == "small"  # F = b2
    assert note_tier(0, E) == "small"  # C = b6


def test_note_tier_none_root_all_big():
    for pc in range(12):
        assert note_tier(pc, None) == "big"


def test_build_axis_strip_exact_size_and_rgba(tmp_path: Path):
    out = tmp_path / "axis.png"
    build_axis_strip(out, width=W, basefreq=BASE, endfreq=END, root_pc=4)
    img = Image.open(out)
    assert img.size == (W, AXIS_H) and img.mode == "RGBA"
    assert img.getchannel("A").getextrema()[0] == 0  # transparent bg present


def test_build_axis_strip_keyless_ok(tmp_path: Path):
    out = tmp_path / "axis_neutral.png"
    build_axis_strip(out, width=W, basefreq=BASE, endfreq=END, root_pc=None)
    assert Image.open(out).size == (W, AXIS_H)


def test_note_color_reserves_hue_for_root_and_flat5():
    """Only the root and the b5 get a hue; everything else is a neutral ramp.

    Size encodes scale membership and brightness agrees with it, so a coloured
    label always means "this note has a name worth knowing".
    """
    from bassify.render.labels import _BLUE, _GOLD, _RAMP, note_color

    E = 4
    assert note_color(4, E) == _GOLD  # root
    assert note_color(10, E) == _BLUE  # b5
    assert note_color(7, E) == _RAMP["big"]  # b3
    assert note_color(6, E) == _RAMP["mid"]  # 2
    assert note_color(5, E) == _RAMP["small"]  # b2
    # Neutral labels (no key detected) never get a hue.
    assert all(note_color(pc, None) == _RAMP["big"] for pc in range(12))


def test_root_label_fits_its_cell(tmp_path: Path):
    """A 3-glyph root label (letter + accidental + octave) must fit one semitone
    cell, or it collides with the next gridline and clips at the frame edge.

    This is why the accidental and octave digit are drawn at _ACC_SIZE: the font
    is monospace, so using U+266F instead of '#' saves no width on its own.
    """
    from bassify.render.labels import _ACC_SIZE, _SHARP, _SIZE, _font

    cell = W / (12 * math.log2(END * FRAME_PAD / (BASE / FRAME_PAD)))
    for tier in _SIZE:
        letter = _font(None, _SIZE[tier]).getlength("C")
        acc = _font(None, _ACC_SIZE[tier]).getlength(_SHARP)
        octave = _font(None, _ACC_SIZE[tier]).getlength("2")
        assert letter + acc + octave < cell, f"{tier} root label overflows its cell"
