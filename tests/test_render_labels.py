from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

from bassify.render.labels import AXIS_H, build_axis_strip, note_tier, note_x

BASE, END, W = 65.41, 261.63, 1280  # C2..C4


def test_note_x_endpoints():
    assert note_x(BASE, BASE, END, W) == 0.0
    assert note_x(END, BASE, END, W) == W


def test_note_x_one_octave():
    assert abs(note_x(2 * BASE, BASE, END, W) - W / math.log2(END / BASE)) < 1e-6


def test_axis_h_is_48():
    assert AXIS_H == 48


def test_note_tier_with_root_E():
    E = 4
    assert note_tier(4, E) == "big"
    assert note_tier(7, E) == "big"    # G = b3
    assert note_tier(9, E) == "big"    # A = 4
    assert note_tier(11, E) == "big"   # B = 5
    assert note_tier(2, E) == "big"    # D = b7
    assert note_tier(10, E) == "med"   # A# = b5
    assert note_tier(5, E) == "small"  # F


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
