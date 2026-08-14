from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from bassify.render.metadata import TrackMeta

NUMBER_SIZE = 48
NAME_SIZE = 72
ARTIST_SIZE = 40


def build_thumbnail(
    cover_png: Path,
    out_png: Path,
    meta: TrackMeta,
    font_path: str,
    width: int = 1280,
    height: int = 720,
) -> Path:
    """Full art + burned track number/name/artist, centered, ~2/3 down."""
    out_png = Path(out_png)
    art = Image.open(cover_png).convert("RGB").resize((width, height))
    draw = ImageDraw.Draw(art, "RGBA")

    # role-based sizes: each field carries its own size regardless of which are present
    rows: list[tuple[str, int]] = []
    if meta.number:
        rows.append((meta.number, NUMBER_SIZE))
    if meta.name:
        rows.append((meta.name, NAME_SIZE))
    if meta.artist:
        rows.append((meta.artist, ARTIST_SIZE))

    fonts = [ImageFont.truetype(font_path, size) for _, size in rows]
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
