"""Trapezoidal gate: full pass [start, cutoff], then linear ramp 1->0 over
[cutoff, fade_end] where fade_end = (cutoff + bass_onset)/2. Smooths the hard
cutoff into the bass-only region.
"""

import json
import subprocess

BASS = "out/BluesBass/03_Turnarounds/bass.wav"
ORIG = "tracks/BluesBass/03_Turnarounds.mp3"

# window start -> (cutoff, bass_onset)
CUTS = {
    0.0: (6.354, 6.612),
    18.486: (24.933, 25.195),
    37.0734: (43.648, 43.912),
    55.9751: (62.271, 62.510),
    74.4015: (80.546, 80.798),
    92.7706: (99.148, 99.407),
    111.301: (118.108, 118.572),
}


def ramp_expr(start, cutoff, fade_end):
    """volume term: 1 in [start,cutoff], linear 1->0 in [cutoff,fade_end]."""
    full = f"between(t,{start:.3f},{cutoff:.3f})"
    ramp = (
        f"between(t,{cutoff:.3f},{fade_end:.3f})"
        f"*(1-(t-{cutoff:.3f})/({fade_end:.3f}-{cutoff:.3f}))"
    )
    return f"({full}+{ramp})"


def build(frac, out):
    terms = []
    for s, (c, bass) in CUTS.items():
        fade_end = c + (bass - c) * frac
        terms.append(ramp_expr(s, c, fade_end))
    gate = "+".join(terms)
    fg = (
        f"[1:a]pan=mono|c0=0.5*c0+0.5*c1,volume='{gate}':eval=frame[gap];"
        f"[0:a][gap]amix=inputs=2:normalize=0[out]"
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-y", "-i", BASS, "-i", ORIG,
         "-filter_complex", fg, "-map", "[out]", "-vn", "-c:a", "pcm_s24le", out],
        capture_output=True,
    )
    print(f"wrote {out} (fade to {frac * 100:.0f}% toward bass)")


# frac 0.5 = halfway to bass (user's suggestion)
build(0.5, "out/BluesBass/03_Turnarounds/combined_fade50.wav")
build(1.0, "out/BluesBass/03_Turnarounds/combined_fade100.wav")
