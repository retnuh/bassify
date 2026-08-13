"""Two smoothing approaches for the hard cutoff, both guitar-safe (the fade
happens AT/BEFORE the cutoff, never passing the post-cutoff guitar):

A) micro fade-out on the gated original, ending at the cutoff.
B) crossfade: original fades out over the last F ms before cutoff, while the
   bass is briefly boosted so total loudness stays continuous.
"""

import json
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
STARTS = {
    0.0: 0.0,
    18.486: 18.486,
    37.0734: 37.0734,
    55.9751: 55.9751,
    74.4015: 74.4015,
    92.7706: 92.7706,
    111.301: 111.301,
}


def gate_A(fade):
    """Original gate with a linear fade-out over [cutoff-fade, cutoff]."""
    terms = []
    for s, c in CUTS.items():
        full = f"between(t,{s:.3f},{c - fade:.3f})"
        ramp = f"between(t,{c - fade:.3f},{c:.3f})*(({c:.3f}-t)/{fade:.3f})"
        terms.append(f"({full}+{ramp})")
    return "+".join(terms)


def build_A(fade, out):
    gate = gate_A(fade)
    fg = (
        f"[1:a]pan=mono|c0=0.5*c0+0.5*c1,volume='{gate}':eval=frame[gap];"
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
    print(f"wrote {out} (A: original fade-out {fade * 1000:.0f}ms)")


def build_B(fade, boost, out):
    """Original fades out over last `fade`s before cutoff; bass gets a matching
    volume bump over the same region so total loudness stays continuous."""
    orig_gate = gate_A(fade)
    # bass boost gate: 1 normally, ramps to (1+boost) across the fade region,
    # then back to 1 after cutoff
    bass_terms = ["1"]
    for _, c in CUTS.items():
        bass_terms.append(
            f"+{boost}*between(t,{c - fade:.3f},{c:.3f})*((t-{c - fade:.3f})/{fade:.3f})"
        )
    bass_gate = "".join(bass_terms)
    fg = (
        f"[1:a]pan=mono|c0=0.5*c0+0.5*c1,volume='{orig_gate}':eval=frame[gap];"
        f"[0:a]volume='{bass_gate}':eval=frame[bb];"
        f"[bb][gap]amix=inputs=2:normalize=0[out]"
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
    print(f"wrote {out} (B: fade {fade * 1000:.0f}ms + bass boost {boost})")


build_A(0.03, "out/BluesBass/03_Turnarounds/smooth_A30.wav")
build_A(0.06, "out/BluesBass/03_Turnarounds/smooth_A60.wav")
build_B(0.06, 0.5, "out/BluesBass/03_Turnarounds/smooth_B60.wav")
