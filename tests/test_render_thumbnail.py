from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from PIL import Image

from bassify.render.metadata import TrackMeta
from bassify.render.thumbnail import (
    ARTIST_SIZE,
    BPM_SIZE,
    MIN_SIZE,
    NAME_SIZE,
    TEXT_MARGIN,
    _fit_font,
    build_thumbnail,
)


def _art(path: Path) -> None:
    Image.new("RGB", (400, 400), (30, 30, 30)).save(path)


def _font() -> str:
    return str(files("bassify.render.fonts") / "DejaVuSansMono.ttf")


def test_thumbnail_size(tmp_path: Path):
    art = tmp_path / "cover.jpg"
    _art(art)
    out = tmp_path / "thumb.png"
    build_thumbnail(art, out, TrackMeta("03", "Turnarounds", "Ed Friedland"), _font())
    assert Image.open(out).size == (1280, 720)


def test_thumbnail_missing_lines_ok(tmp_path: Path):
    art = tmp_path / "cover.jpg"
    _art(art)
    out = tmp_path / "thumb.png"
    build_thumbnail(art, out, TrackMeta(None, "Foo", None), _font())
    assert out.exists()


def test_thumbnail_no_number_name_larger_than_artist(tmp_path: Path):
    """When number is absent, name must render at NAME_SIZE (72) and artist at ARTIST_SIZE (40)."""
    assert NAME_SIZE > ARTIST_SIZE  # sanity: role sizes are ordered correctly

    art = tmp_path / "cover.jpg"
    _art(art)
    out = tmp_path / "thumb.png"
    build_thumbnail(art, out, TrackMeta(None, "Some Name", "Some Artist"), _font())
    assert Image.open(out).size == (1280, 720)


def test_thumbnail_bpm_row_present(tmp_path: Path):
    assert ARTIST_SIZE > BPM_SIZE  # sanity: BPM is the smallest role

    art = tmp_path / "cover.jpg"
    _art(art)
    out = tmp_path / "thumb.png"
    build_thumbnail(art, out, TrackMeta("03", "Turnarounds", "Ed Friedland", bpm=117.45), _font())
    assert Image.open(out).size == (1280, 720)


def test_thumbnail_missing_bpm_ok(tmp_path: Path):
    art = tmp_path / "cover.jpg"
    _art(art)
    out = tmp_path / "thumb.png"
    build_thumbnail(art, out, TrackMeta("03", "Turnarounds", "Ed Friedland"), _font())
    assert Image.open(out).size == (1280, 720)


def test_thumbnail_preserves_aspect_ratio_with_black_padding(tmp_path: Path):
    """Non-16:9 cover art must be letterboxed/pillarboxed, not stretched.

    A plain resize((1280, 720)) would smear a tall portrait cover across the
    full frame; ImageOps.pad instead scales it to fit and fills the remainder
    with black, leaving the art's own aspect ratio intact.
    """
    art = tmp_path / "cover.jpg"
    # Tall portrait art -- narrower than 16:9, so padding lands on left/right.
    Image.new("RGB", (400, 800), (200, 50, 50)).save(art)
    out = tmp_path / "thumb.png"
    build_thumbnail(art, out, TrackMeta(None, None, None), _font())

    img = Image.open(out).convert("RGB")
    assert img.size == (1280, 720)
    # Corners fall outside the scaled art (pillarboxed) -> padded black.
    assert img.getpixel((0, 0)) == (0, 0, 0)
    assert img.getpixel((1279, 0)) == (0, 0, 0)
    # Center falls inside the scaled art, above the text-overlay band
    # (~0.62*720=446) -> the source colour, modulo JPEG's lossy compression.
    r, g, b = img.getpixel((640, 300))
    assert (abs(r - 200), abs(g - 50), abs(b - 50)) < (3, 3, 3)


def test_fit_font_shrinks_long_text_to_stay_in_bounds():
    """A title long enough to overflow at its role size must come back at a
    smaller size that actually fits, not the original overflowing size.

    Regression case: "The Thrill Is Gone (Bass Only)" at NAME_SIZE (72)
    overran both edges of a 1280px frame before this fit-to-width step existed.
    """
    text = "The Thrill Is Gone (Bass Only)"
    max_width = 1280 - 2 * TEXT_MARGIN
    font = _fit_font(text, NAME_SIZE, _font(), max_width)

    assert font.size < NAME_SIZE
    assert font.size >= MIN_SIZE
    assert font.getlength(text) <= max_width


def test_fit_font_leaves_short_text_at_role_size():
    font = _fit_font("40", NAME_SIZE, _font(), 1280 - 2 * TEXT_MARGIN)
    assert font.size == NAME_SIZE


def test_thumbnail_long_name_does_not_overflow_frame(tmp_path: Path):
    art = tmp_path / "cover.jpg"
    _art(art)
    out = tmp_path / "thumb.png"
    meta = TrackMeta("40", "The Thrill Is Gone (Bass Only)", "Ed Friedland", bpm=89.1)
    build_thumbnail(art, out, meta, _font())
    assert Image.open(out).size == (1280, 720)
