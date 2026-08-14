from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from bassify.render.metadata import TrackMeta

_SIZES = (48, 72, 40)  # number / name / artist


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

    lines = meta.display_lines()
    sizes = [72] if len(lines) == 1 else list(_SIZES)
    fonts = [ImageFont.truetype(font_path, sizes[i]) for i in range(len(lines))]
    heights = [draw.textbbox((0, 0), t, font=f)[3] - draw.textbbox((0, 0), t, font=f)[1]
               for t, f in zip(lines, fonts, strict=True)]

    gap = 16
    y = int(height * 0.62)
    draw.rectangle([0, y - 20, width, height], fill=(0, 0, 0, 120))
    for text, font, h in zip(lines, fonts, heights, strict=True):
        bb = draw.textbbox((0, 0), text, font=font)
        draw.text(((width - (bb[2] - bb[0])) / 2, y), text, font=font,
                  fill=(255, 255, 255, 255))
        y += h + gap

    art.save(out_png)
    return out_png
