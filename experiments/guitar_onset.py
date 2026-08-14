"""Correct algorithm: clicks appear in BOTH bass and original; guitar appears
ONLY in the original (it cancels in L-R, so it's absent from bass). So the
guitar onset = the original onset near the window end that has NO matching bass
onset. Cut just before it.
"""

import json
import warnings

import librosa

warnings.filterwarnings("ignore")

BASS = "out/BluesBass/03_Turnarounds/bass.wav"
ORIG = "tracks/BluesBass/03_Turnarounds.mp3"
WINS = json.load(open("out/BluesBass/03_Turnarounds/windows.json"))

yb, sr = librosa.load(BASS, sr=None, mono=True)
yo, sro = librosa.load(ORIG, sr=None, mono=True)


def onsets(y, s, ws, we):
    seg = y[int(ws * s) : int(we * s)]
    env = librosa.onset.onset_strength(y=seg, sr=s)
    fr = librosa.onset.onset_detect(onset_envelope=env, sr=s, backtrack=True, units="frames")
    return [librosa.frames_to_time(f, sr=s) + ws for f in fr]


MATCH = 0.05  # a bass onset within this of an original onset = same event (a click)
MARGIN = 0.02  # cut this far before the guitar onset


for i, w in enumerate(WINS[:7]):
    ws, we = w["start"], w["end"]
    bass = onsets(yb, sr, ws, we)
    orig = [t for t in onsets(yo, sro, ws, we) if t < we - 0.02]
    # guitar candidates: original onsets with NO bass onset within MATCH
    guitar_onsets = [t for t in orig if not any(abs(t - bt) < MATCH for bt in bass)]
    # the operative guitar onset is the LAST one before the bass downbeat that
    # sits in the count-in tail (after the last matched click)
    matched = [t for t in orig if any(abs(t - bt) < MATCH for bt in bass)]
    last_click = matched[-1] if matched else (bass[-1] if bass else ws)
    # guitar = first unmatched original onset AFTER the last click
    guitar = next((t for t in guitar_onsets if t > last_click + 0.01), None)
    if guitar:
        cut = guitar - MARGIN
        print(
            f"win{i} last_click={last_click:.3f} guitar={guitar:.3f} bass={we:.3f} "
            f"CUT={cut:.3f} (keeps {(cut - last_click) * 1000:+.0f}ms past click)"
        )
    else:
        cut = min(we, last_click + 0.10)
        print(
            f"win{i} last_click={last_click:.3f} NO guitar-only onset bass={we:.3f} "
            f"CUT={cut:.3f} (fallback click+100ms)"
        )
