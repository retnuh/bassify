"""Replace the 6th (last) click region with a copy of the clean 5th click.

The 6th click overlaps the guitar; the 5th is clean (guitar-free). So paste the
5th click's audio starting at the 6th click's onset, then silence to the bass
onset. Result: a complete, clean final count with no guitar bleed.

Built on the ORIGINAL + BASS directly (win0), mirroring the combine gate but
swapping the last click's source.
"""

import librosa
import numpy as np
import soundfile as sf

ORIG = "tracks/BluesBass/03_Turnarounds.mp3"
BASS = "out/BluesBass/03_Turnarounds/bass.wav"

# win0 landmarks
CLICK5 = 5.561
CLICK6 = 6.211
BASS_ON = 6.612
CUT = 6.369  # current clean-cut point (guitar-5ms), for reference

yo, sr = librosa.load(ORIG, sr=None, mono=True)
yb, _ = librosa.load(BASS, sr=None, mono=True)


def seg(y, t0, t1):
    return y[int(t0 * sr) : int(t1 * sr)].copy()


def build(click5_len, out):
    # base = bass track (ducked to 0 in the whole win0 gap, ramp to 1 at bass on)
    y = yb.copy()
    gs, ge = 0.0, BASS_ON
    ramp = 0.15
    a, b = int(gs * sr), int((BASS_ON - ramp) * sr)
    y[a:b] = 0.0
    r0, r1 = int((BASS_ON - ramp) * sr), int(BASS_ON * sr)
    y[r0:r1] *= np.linspace(0, 1, r1 - r0)

    # original speech/clicks passes [start, CLICK6] as-is (full band)
    o = np.zeros_like(y)
    a2, b2 = int(gs * sr), int(CLICK6 * sr)
    o[a2:b2] = yo[a2:b2]

    # paste the clean 5th click at the 6th onset
    donor = seg(yo, CLICK5, CLICK5 + click5_len)
    fade = int(0.01 * sr)
    donor[:fade] *= np.linspace(0, 1, fade)
    donor[-fade:] *= np.linspace(1, 0, fade)
    p = int(CLICK6 * sr)
    o[p : p + len(donor)] += donor
    # (everything after donor stays 0 = silence until bass)

    mix = y + o
    sf.write(out, mix, sr, subtype="PCM_24")
    print(f"wrote {out} (5th click {click5_len * 1000:.0f}ms pasted at 6th onset)")


build(0.20, "out/BluesBass/03_Turnarounds/swap5_200.wav")
build(0.30, "out/BluesBass/03_Turnarounds/swap5_300.wav")
