"""Simplest cut: last onset before music start = last click. Cut between it and
the music (window end = bass onset = music downbeat).

We do NOT count clicks or validate rhythm. We only need the boundary between the
final count click and the music.
"""

import json

import librosa

BASS = "out/BluesBass/03_Turnarounds/bass.wav"
ORIG = "tracks/BluesBass/03_Turnarounds.mp3"
WINS = json.load(open("out/BluesBass/03_Turnarounds/windows.json"))

yb, sr = librosa.load(BASS, sr=None, mono=True)


def onsets_in(y, start, end, sr):
    a, b = int(start * sr), int(end * sr)
    seg = y[a:b]
    env = librosa.onset.onset_strength(y=seg, sr=sr)
    fr = librosa.onset.onset_detect(onset_envelope=env, sr=sr, backtrack=True, units="frames")
    return [librosa.frames_to_time(f, sr=sr) + start for f in fr]


OFFSET = 0.100  # cut this long after the last bass click (before guitar ~150ms)

for i, w in enumerate(WINS):
    ws, we = w["start"], w["end"]
    # BASS onsets = clicks only (guitar + speech cancelled in L-R subtraction)
    bass_ons = [t for t in onsets_in(yb, ws, we, sr) if t < we - 0.05]
    if not bass_ons:
        print(f"win{i} [{ws:.2f}-{we:.2f}] no bass click -> keep end {we:.3f}")
        continue
    last_click = bass_ons[-1]
    cut = min(we, last_click + OFFSET)
    print(
        f"win{i} [{ws:.2f}-{we:.2f}] last_click={last_click:.3f} bass={we:.3f} "
        f"CUT={cut:.3f} (delta {(cut - we) * 1000:+.0f}ms, "
        f"click->bass {(we - last_click) * 1000:.0f}ms)"
    )
