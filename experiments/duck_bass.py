"""Timbre-match: the bass track during a gap is pure L-R residue (muddy slosh),
since real bass only starts at the window END. Duck the bass DOWN during the
gap so its silence matches the clean original room-tone, then ramp it back up to
full right at the bass onset (window end). This removes the muddy residue under
the speech AND turns the cutoff into a bass fade-in instead of a timbre switch.

Combined = ducked-bass + gated-original(speech/clicks up to guitar cutoff).
"""

import subprocess

BASS = "out/BluesBass/03_Turnarounds/bass.wav"
ORIG = "tracks/BluesBass/03_Turnarounds.mp3"

# window: start, cutoff (guitar), bass_onset (window end)
WINS = [
    (0.0, 6.354, 6.612),
    (18.486, 24.933, 25.195),
    (37.0734, 43.648, 43.912),
    (55.9751, 62.271, 62.510),
    (74.4015, 80.546, 80.798),
    (92.7706, 99.148, 99.407),
    (111.301, 118.108, 118.572),
]
FADE = 0.06  # original fade-out before cutoff


def orig_gate():
    terms = []
    for s, c, _ in WINS:
        full = f"between(t,{s:.3f},{c - FADE:.3f})"
        ramp = f"between(t,{c - FADE:.3f},{c:.3f})*(({c:.3f}-t)/{FADE:.3f})"
        terms.append(f"({full}+{ramp})")
    return "+".join(terms)


def bass_gain(duck, rampup):
    """Bass gain = duck during each gap [start, bass_onset-rampup], then ramp
    duck->1 over [bass_onset-rampup, bass_onset]. Full (1) everywhere else."""
    # start at 1; for each window subtract (1-duck) during the gap, then restore
    terms = ["1"]
    for s, _c, b in WINS:
        gap_end = b - rampup
        # during [s, gap_end]: gain = duck  -> subtract (1-duck)
        terms.append(f"-{1 - duck}*between(t,{s:.3f},{gap_end:.3f})")
        # during [gap_end, b]: linear duck->1 -> subtract (1-duck)*(1 - progress)
        terms.append(
            f"-{1 - duck}*between(t,{gap_end:.3f},{b:.3f})*(1-((t-{gap_end:.3f})/{rampup:.3f}))"
        )
    return "".join(terms)


def build(duck, rampup, out):
    og = orig_gate()
    bg = bass_gain(duck, rampup)
    fg = (
        f"[1:a]pan=mono|c0=0.5*c0+0.5*c1,volume='{og}':eval=frame[gap];"
        f"[0:a]volume='{bg}':eval=frame[bd];"
        f"[bd][gap]amix=inputs=2:normalize=0[out]"
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
    print(f"wrote {out} (duck bass to {duck} in gaps, {rampup * 1000:.0f}ms rampup)")


# duck bass hard (0.1) vs moderate (0.3); 150ms rampup into bass onset
build(0.1, 0.15, "out/BluesBass/03_Turnarounds/duck10.wav")
build(0.3, 0.15, "out/BluesBass/03_Turnarounds/duck30.wav")
