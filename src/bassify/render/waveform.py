from __future__ import annotations

from pathlib import Path

from bassify.ffmpeg import run_ffmpeg
from bassify.render.filtergraph import WAVE_STRIP_H


def render_waveform_pic(
    bass_wav: Path,
    out_png: Path,
    width: int,
    height: int = WAVE_STRIP_H,
    color: str = "cyan",
) -> Path:
    """Whole-track showwavespic PNG. scale=cbrt fills the strip with quiet bass
    (linear leaves ~16px of 80; cbrt ~46px)."""
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-i",
            str(bass_wav),
            "-filter_complex",
            f"showwavespic=s={width}x{height}:colors={color}:scale=cbrt",
            "-frames:v",
            "1",
            "-update",
            "1",
            str(out_png),
        ]
    )
    return out_png
