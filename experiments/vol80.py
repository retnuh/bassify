"""B60 smoothing but with the mixed-in original at reduced volume, so the drop
at the cutoff is a smaller step. Test 0.8 and 0.7."""

import subprocess

BASS = "out/BluesBass/03_Turnarounds/bass.wav"
ORIG = "tracks/BluesBass/03_Turnarounds.mp3"

CUTS = {
    0.0: 6.354,
    18.486: 24.933,
    37.0734: 43.648,
    55.9751: 62.271,
    74.4015: 80.546,
    92.7706: 99.148,
    111.301: 118.108,
}
FADE = 0.06


def gate(fade):
    terms = []
    for s, c in CUTS.items():
        full = f"between(t,{s:.3f},{c - fade:.3f})"
        ramp = f"between(t,{c - fade:.3f},{c:.3f})*(({c:.3f}-t)/{fade:.3f})"
        terms.append(f"({full}+{ramp})")
    return "+".join(terms)


def build(vol, out):
    g = gate(FADE)
    # original scaled by vol; the volume gate multiplies on top
    fg = (
        f"[1:a]pan=mono|c0=0.5*c0+0.5*c1,volume='{vol}*({g})':eval=frame[gap];"
        f"[0:a][gap]amix=inputs=2:normalize=0[out]"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            BASS,
            "-i",
            ORIG,
            "-filter_complex",
            fg,
            "-map",
            "[out]",
            "-vn",
            "-c:a",
            "pcm_s24le",
            out,
        ],
        capture_output=True,
    )
    print(f"wrote {out} (original @ {vol})")


build(0.8, "out/BluesBass/03_Turnarounds/vol80.wav")
build(0.7, "out/BluesBass/03_Turnarounds/vol70.wav")
