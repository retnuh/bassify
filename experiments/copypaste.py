"""Copy-paste the tail of a CLEAN earlier click onto the last click, so the last
click sounds complete without pulling in the guitar.

Approach for win0: the combined (duck00) cuts the original at guitar-5ms (6.369),
clipping the last click. We take a clean donor click (an earlier count click with
a guitar-free tail) and mix its waveform in at the last-click onset, extending the
click naturally past the cut.

Builds on the already-correct combined_real.wav by overlaying a donor click.
"""

import librosa
import numpy as np
import soundfile as sf

ORIG = "tracks/BluesBass/03_Turnarounds.mp3"
COMBINED = "out/BluesBass/03_Turnarounds/combined_real.wav"

# win0: last click onset, guitar onset, donor click onset
LAST_CLICK = 6.211
GUITAR = 6.374
DONOR = 5.561  # clean click (long gap after, no guitar)

yo, sr = librosa.load(ORIG, sr=None, mono=True)
yc, src = librosa.load(COMBINED, sr=None, mono=True)
assert sr == src, (sr, src)


def seg(y, t0, dur):
    a = int(t0 * sr)
    return y[a : a + int(dur * sr)]


def build(donor_len, out):
    """Overlay the donor click (from ORIG) at the last-click onset in COMBINED.

    The combined already has the last click up to the cut; we ADD the donor's
    tail portion (from donor onset) starting at the last-click onset so the ring
    continues naturally. A short fade on the donor avoids a seam.
    """
    y = yc.copy()
    donor = seg(yo, DONOR, donor_len).copy()
    # fade in/out the donor so it blends
    fade = int(0.01 * sr)
    if len(donor) > 2 * fade:
        donor[:fade] *= np.linspace(0, 1, fade)
        donor[-fade:] *= np.linspace(1, 0, fade)
    # place donor starting at LAST_CLICK
    a = int(LAST_CLICK * sr)
    b = a + len(donor)
    y[a:b] += donor * 0.8  # scale donor a touch
    sf.write(out, y, sr, subtype="PCM_24")
    print(f"wrote {out} (donor {donor_len * 1000:.0f}ms from {DONOR}s)")


build(0.35, "out/BluesBass/03_Turnarounds/cp_350.wav")
build(0.50, "out/BluesBass/03_Turnarounds/cp_500.wav")
