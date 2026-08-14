"""Prototype: split-band gate. Count-in body passes full original; the
click/guitar overlap tail passes a HIGHPASSED original (guitar body cut, click
transient survives). Test on win0 and win6.

Builds one combined per (window, hp cutoff) for A/B listening.
"""

import subprocess

BASS = "out/BluesBass/03_Turnarounds/bass.wav"
ORIG = "tracks/BluesBass/03_Turnarounds.mp3"

# window: (start, body_end, tail_end, bass_onset)
# body_end = last click onset (full band up to here)
# tail_end = where we stop passing anything (just before bass / cut point)
WINDOWS = {
    "win0": dict(start=0.0, body_end=6.246, tail_end=6.45, label="win0"),
    "win6": dict(start=111.301, body_end=118.186, tail_end=118.55, label="win6"),
}


def build(win, hp, out):
    s, be, te = win["start"], win["body_end"], win["tail_end"]
    # gate1: full original, active [start, be]
    g1 = f"between(t,{s},{be})"
    # gate2: highpassed original, active [be, te]
    g2 = f"between(t,{be},{te})"
    fg = (
        f"[1:a]pan=mono|c0=0.5*c0+0.5*c1,volume='{g1}':eval=frame[body];"
        f"[2:a]pan=mono|c0=0.5*c0+0.5*c1,highpass=f={hp},volume='{g2}':eval=frame[tail];"
        f"[0:a][body][tail]amix=inputs=3:normalize=0[out]"
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
    print(f"wrote {out} (hp={hp})")


for wname, win in WINDOWS.items():
    for hp in (3000, 4000):
        build(win, hp, f"out/BluesBass/03_Turnarounds/hp_{wname}_{hp}.wav")
