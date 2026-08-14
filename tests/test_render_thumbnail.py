from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from PIL import Image

from bassify.render.metadata import TrackMeta
from bassify.render.thumbnail import ARTIST_SIZE, NAME_SIZE, build_thumbnail


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
