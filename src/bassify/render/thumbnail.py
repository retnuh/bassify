from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from bassify.render.metadata import TrackMeta

NUMBER_SIZE = 48
NAME_SIZE = 72
ARTIST_SIZE = 40
BPM_SIZE = 36

MIN_SIZE = 20  # never shrink a row below this, however long the text
TEXT_MARGIN = 40  # keep burned text off the very left/right edges


def _fit_font(text: str, size: int, font_path: str, max_width: float) -> ImageFont.FreeTypeFont:
    """The role size, or smaller if the text would overflow max_width.

    DejaVuSansMono is monospace, so a glyph's width scales linearly with point
    size -- one measurement at the starting size is enough to compute the
    exact size that fits, no iterative search needed.
    """
    font = ImageFont.truetype(font_path, size)
    width = font.getlength(text)
    if width <= max_width:
        return font
    fitted = max(MIN_SIZE, int(size * max_width / width))
    return ImageFont.truetype(font_path, fitted)


def build_thumbnail(
    cover_png: Path,
    out_png: Path,
    meta: TrackMeta,
    font_path: str,
    width: int = 1280,
    height: int = 720,
) -> Path:
    """Full art + burned track number/name/artist/bpm, centered, ~2/3 down.

    Cover art keeps its own aspect ratio -- scaled to fit inside width x height
    and letterboxed/pillarboxed with black, not stretched. Source art is rarely
    exactly 16:9 (square album art is common), and a plain resize distorts it.
    """
    out_png = Path(out_png)
    art = ImageOps.pad(
        Image.open(cover_png).convert("RGB"), (width, height), color=(0, 0, 0), centering=(0.5, 0.5)
    )
    draw = ImageDraw.Draw(art, "RGBA")

    # role-based sizes: each field carries its own size regardless of which are present
    rows: list[tuple[str, int]] = []
    if meta.number:
        rows.append((meta.number, NUMBER_SIZE))
    if meta.name:
        rows.append((meta.name, NAME_SIZE))
    if meta.artist:
        rows.append((meta.artist, ARTIST_SIZE))
    if meta.bpm is not None:
        rows.append((f"{round(meta.bpm)} BPM", BPM_SIZE))

    max_text_width = width - 2 * TEXT_MARGIN
    fonts = [_fit_font(t, size, font_path, max_text_width) for t, size in rows]
    heights = [
        draw.textbbox((0, 0), t, font=f)[3] - draw.textbbox((0, 0), t, font=f)[1]
        for (t, _), f in zip(rows, fonts, strict=True)
    ]

    gap = 16
    y = int(height * 0.62)
    draw.rectangle([0, y - 20, width, height], fill=(0, 0, 0, 120))
    for (text, _), font, h in zip(rows, fonts, heights, strict=True):
        bb = draw.textbbox((0, 0), text, font=font)
        draw.text(((width - (bb[2] - bb[0])) / 2, y), text, font=font, fill=(255, 255, 255, 255))
        y += h + gap

    art.save(out_png)
    return out_png
