from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from PIL import Image

from bassify.render.metadata import TrackMeta
from bassify.render.thumbnail import build_thumbnail


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
