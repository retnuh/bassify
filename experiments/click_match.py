"""Count-in click cutoff finder.

Insight: the BASS track (L-R) has speech and guitar cancelled, so within a
detected silence window the only events are the count-in clicks plus the bass
onset at the very end. So we LOCATE clicks by finding spikes in the bass inside
each window, then MEASURE their durations on the ORIGINAL (full energy), and
project the cutoff = last-click-onset + avg-of-first-N click durations.
"""

import json
import re
import subprocess
import sys

ORIG = "tracks/BluesBass/03_Turnarounds.mp3"
BASS = "out/BluesBass/03_Turnarounds/bass.wav"
WINS = json.load(open("out/BluesBass/03_Turnarounds/windows.json"))
FRAME = 0.005
N = int(44100 * FRAME)


def envelope(src, start, dur, hp=None, mono=False):
    out = "experiments/_env.txt"
    af = ""
    if mono:
        af += "pan=mono|c0=0.5*c0+0.5*c1,"
    if hp:
        af += f"highpass=f={hp},"
    af += (
        f"asetnsamples=n={N},astats=metadata=1:reset=1,"
        f"ametadata=print:key=lavfi.astats.Overall.RMS_level:file={out}"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-ss",
            f"{start}",
            "-t",
            f"{dur}",
            "-i",
            src,
            "-af",
            af,
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
    )
    rows = []
    t = None
    for line in open(out):
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            t = float(m.group(1))
        m = re.search(r"RMS_level=(-?[0-9.]+|-inf)", line)
        if m and t is not None:
            v = m.group(1)
            db = -120.0 if v == "-inf" else float(v)
            rows.append((start + t, db))
    return rows


def spikes(rows, gate, min_spacing=0.15):
    """Onset times where level rises above gate, debounced by min_spacing."""
    out = []
    prev_above = False
    for t, db in rows:
        above = db > gate
        if above and not prev_above:
            if not out or (t - out[-1]) >= min_spacing:
                out.append(t)
        prev_above = above
    return out


def duration_on(src, onset, mono=False, hp=None, gate=-45, maxdur=0.4):
    """Measure how long the signal stays above gate starting near onset."""
    rows = envelope(src, max(0, onset - 0.02), maxdur, hp=hp, mono=mono)
    started = False
    start_t = None
    last_above = None
    for t, db in rows:
        if db > gate:
            if not started:
                started = True
                start_t = t
            last_above = t
        elif started and (t - last_above) > 0.03:
            break
    if start_t is None:
        return None
    return start_t, last_above - start_t


def process(win, verbose=True):
    ws, we = win["start"], win["end"]
    brows = envelope(BASS, ws, (we - ws))
    raw = spikes(brows, gate=-62, min_spacing=0.15)
    raw = [t for t in raw if t > ws + 0.15]
    click_onsets = [t for t in raw if t < we - 0.20]
    # Drop spurious trailing spikes: a real count beat is >=0.30s from the prior
    # click (the tight end of the count-in is ~0.64s; anything <0.30s at the tail
    # is bass-onset bleed, not a click).
    while len(click_onsets) >= 2 and (click_onsets[-1] - click_onsets[-2]) < 0.30:
        click_onsets.pop()
    # Require a plausible count-in: >=5 clicks. Else fall back to window end.
    if len(click_onsets) < 5:
        if verbose:
            print(
                f"window [{ws:.3f},{we:.3f}] {len(click_onsets)} clicks (not a count-in) "
                f"-> fallback to end {we:.3f}"
            )
        return we, click_onsets
    durs = []
    for on in click_onsets:
        r = duration_on(ORIG, on, mono=True, hp=2000, gate=-45)
        if r:
            durs.append(r[1])
    firstN = durs[:5]
    avg = sum(firstN) / len(firstN) if firstN else 0
    cutoff = click_onsets[-1] + avg
    if verbose:
        gaps = [
            f"{click_onsets[i + 1] - click_onsets[i]:.2f}" for i in range(len(click_onsets) - 1)
        ]
        print(
            f"window [{ws:.3f},{we:.3f}] {len(click_onsets)} clicks gaps=[{','.join(gaps)}] "
            f"avg5={avg * 1000:.0f}ms last={click_onsets[-1]:.3f} CUTOFF={cutoff:.3f} "
            f"(delta {(cutoff - we) * 1000:+.0f}ms)"
        )
    return cutoff, click_onsets


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "all":
        for w in WINS:
            process(w)
        return
    win = WINS[int(sys.argv[1])] if len(sys.argv) > 1 else WINS[0]
    ws, we = win["start"], win["end"]
    print(f"window [{ws:.3f}, {we:.3f}]")

    # 1) locate clicks in the BASS within the window (exclude the final bass
    #    onset by staying a hair before we)
    brows = envelope(BASS, ws, (we - ws))
    # bass clicks ride ~-60dB over a ~-75 floor
    raw = spikes(brows, gate=-62, min_spacing=0.15)
    # drop file-edge artifact: a spike within 150ms of window start (t=0 DC /
    # fade-in block registers as a false spike)
    raw = [t for t in raw if t > ws + 0.15]
    # drop the trailing bass-onset spike: the last spike sits right at the loud
    # window end. Any spike within 0.20s of we is the bass onset, not a click.
    click_onsets = [t for t in raw if t < we - 0.20]
    print(
        f"bass click onsets ({len(click_onsets)}): " + ", ".join(f"{t:.3f}" for t in click_onsets)
    )

    if len(click_onsets) < 2:
        print("too few clicks; fallback to window end")
        return

    # 2) measure durations on the ORIGINAL at those matched onsets (hp to catch
    #    stick transient, mono downmix like combine)
    durs = []
    for on in click_onsets:
        r = duration_on(ORIG, on, mono=True, hp=2000, gate=-45)
        if r:
            st, d = r
            durs.append(d)
            print(f"  click @{on:.3f}: original dur {d * 1000:.0f}ms")
        else:
            print(f"  click @{on:.3f}: no original energy")

    # 3) project cutoff = last onset + avg of first-5 durations
    if len(durs) >= 1:
        firstN = durs[:5]
        avg = sum(firstN) / len(firstN)
        last_onset = click_onsets[-1]
        cutoff = last_onset + avg
        print(f"\navg dur first {len(firstN)} = {avg * 1000:.0f}ms")
        print(f"last click onset = {last_onset:.3f}")
        print(
            f"CUTOFF = {cutoff:.3f}  (window end was {we:.3f}, delta {(cutoff - we) * 1000:+.0f}ms)"
        )


if __name__ == "__main__":
    main()
