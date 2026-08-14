"""Prototype: use librosa onset detection to find count-in clicks per window.

Compares against our hand-validated result (window 0 cutoff ~6.343). Detects
onsets on the BASS track within each silence window (speech+guitar cancelled),
then cutoff = last_onset + a small tail so the final click is preserved but the
downbeat (which coincides with bass onset / window end) is excluded.
"""

import json

import librosa

BASS = "out/BluesBass/03_Turnarounds/bass.wav"
WINS = json.load(open("out/BluesBass/03_Turnarounds/windows.json"))

# Load whole bass track once
y, sr = librosa.load(BASS, sr=None, mono=True)
print(f"loaded bass: {len(y)} samples @ {sr}Hz = {len(y) / sr:.2f}s\n")


def onsets_in(start, end):
    a = int(start * sr)
    b = int(end * sr)
    seg = y[a:b]
    # onset envelope + peak picking; backtrack to local minima for true starts
    env = librosa.onset.onset_strength(y=seg, sr=sr)
    frames = librosa.onset.onset_detect(onset_envelope=env, sr=sr, backtrack=True, units="frames")
    times = librosa.frames_to_time(frames, sr=sr) + start
    return times


def merge_close(times, min_sep=0.20):
    """Collapse onsets closer than min_sep (click attack+ring doublets)."""
    if len(times) == 0:
        return []
    out = [times[0]]
    for t in times[1:]:
        if t - out[-1] >= min_sep:
            out.append(t)
    return out


def is_countin(clicks, min_clicks=5, min_gap=0.30, max_gap=2.0):
    if len(clicks) < min_clicks:
        return False
    for j in range(1, len(clicks)):
        g = clicks[j] - clicks[j - 1]
        if g < min_gap or g > max_gap:
            return False
    return True


for i, w in enumerate(WINS):
    ws, we = w["start"], w["end"]
    ons = onsets_in(ws, we)
    clicks = [t for t in ons if t < we - 0.20]
    clicks = merge_close(clicks)
    gaps = [f"{clicks[j + 1] - clicks[j]:.2f}" for j in range(len(clicks) - 1)]
    ci = is_countin(clicks)
    verdict = ""
    if ci:
        last = clicks[-1]
        # tail = median of the tight end-gaps; cutoff = last + tail
        tail = 0.15
        cutoff = min(we, last + tail)
        verdict = (
            f"COUNT-IN last={last:.3f} CUTOFF={cutoff:.3f} (delta {(cutoff - we) * 1000:+.0f}ms)"
        )
    else:
        verdict = "not a count-in -> fallback to end"
    print(f"win{i} [{ws:.2f}-{we:.2f}] {len(clicks)} clicks gaps=[{','.join(gaps)}]  {verdict}")
