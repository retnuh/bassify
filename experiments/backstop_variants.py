"""Render backstop-slope listening variants from an existing bass_clean.wav.

Applies additional lowpass stages on top of the already-filtered clean bass,
cuts a listening window, and writes m4a files for A/B comparison. Used to find
the point where more slope stops helping (or starts dulling the bass) before
committing to a backstop design.

Usage: uv run python experiments/backstop_variants.py "40_The Thrill Is Gone" ...
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt, sosfiltfilt

START = 5.0
DUR = 30.0
OUT_DIR = Path("experiments")

# label -> list of (cutoff_hz, butterworth order, zero_phase) stages applied in
# sequence. A cascade of order-2 causal stages matches ffmpeg's `lowpass` chain.
VARIANTS: dict[str, list[tuple[float, int, bool]]] = {
    "A_current_4pole": [],
    "C_12pole_800": [(800.0, 2, False)] * 4,
    "D_24pole_800": [(800.0, 2, False)] * 10,
    "E_zerophase_o8_800": [(800.0, 8, True)],
    "F_12pole_600": [(600.0, 2, False)] * 4,
    "G_12pole_1000": [(1000.0, 2, False)] * 4,
    "H_12pole_1200": [(1200.0, 2, False)] * 4,
}

# label -> ffmpeg -af string, for shapes scipy butterworth can't express.
# Shelves attenuate the guitar band instead of deleting it, keeping some bass
# harmonic character.
FFMPEG_VARIANTS: dict[str, str] = {
    "I_shelf_800_m12db": "highshelf=f=800:g=-12",
    "J_shelf_800_m20db": "highshelf=f=800:g=-20",
}


def render(src: Path, track: str, label: str, stages: list) -> Path:
    y, sr = sf.read(str(src), dtype="float64", always_2d=True)
    for cutoff, order, zero_phase in stages:
        sos = butter(order, cutoff / (sr / 2), btype="low", output="sos")
        filt = sosfiltfilt if zero_phase else sosfilt
        y = np.stack([filt(sos, y[:, ch]) for ch in range(y.shape[1])], axis=1)

    start = int(START * sr)
    y = y[start : start + int(DUR * sr)]

    num = track.split("_", 1)[0]
    tmp = OUT_DIR / f"_tmp_{num}_{label}.wav"
    out = OUT_DIR / f"{num}_{label}.m4a"
    sf.write(str(tmp), y, sr, subtype="PCM_24")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(tmp),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(out),
        ],
        check=True,
    )
    tmp.unlink()
    return out


def render_ffmpeg(src: Path, track: str, label: str, af: str) -> Path:
    num = track.split("_", 1)[0]
    out = OUT_DIR / f"{num}_{label}.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(START),
            "-t",
            str(DUR),
            "-i",
            str(src),
            "-af",
            af,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(out),
        ],
        check=True,
    )
    return out


def main() -> None:
    tracks = sys.argv[1:]
    if not tracks:
        sys.exit("usage: backstop_variants.py <track dir name> ...")
    for track in tracks:
        src = Path("out/BluesBass") / track / f"{track}_bass_clean.wav"
        if not src.exists():
            sys.exit(f"missing {src}")
        for label, stages in VARIANTS.items():
            print(render(src, track, label, stages))
        for label, af in FFMPEG_VARIANTS.items():
            print(render_ffmpeg(src, track, label, af))


if __name__ == "__main__":
    main()
