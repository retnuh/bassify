"""Count-in click cutoff refinement.

Within a bass-silence window, count-in clicks appear as spikes in the BASS
track (L-R subtraction cancels speech and guitar). We locate those spikes,
measure each click's duration on the ORIGINAL mix, then project:

    cutoff = last_click_onset + mean(first_5_click_durations)

This places the gate end AFTER the final count click but BEFORE the music
downbeat. Falls back to ``window_end`` when no valid count-in is detected.
"""

from __future__ import annotations

import re
import statistics
import subprocess
import tempfile
from pathlib import Path

# Frame size for envelope extraction: 5 ms at 44100 Hz
_FRAME_S = 0.005
_N_SAMPLES = int(44100 * _FRAME_S)  # 220 samples per frame


# ---------------------------------------------------------------------------
# Pure functions — unit-testable without ffmpeg
# ---------------------------------------------------------------------------


def adaptive_gate(frame_dbs: list[float], margin: float = 12.0) -> float:
    """Return spike gate = median(frame_dbs) + margin.

    Using the median means a "mic tone" background shift (which raises the
    whole floor) is correctly captured, while a fixed gate would fail on noisy
    windows. Falls back to -60 dB when the list is empty.
    """
    if not frame_dbs:
        return -60.0
    return statistics.median(frame_dbs) + margin


def spikes_from_envelope(
    rows: list[tuple[float, float]],
    gate: float,
    min_spacing: float = 0.15,
) -> list[float]:
    """Return onset times where level rises above gate, debounced by min_spacing.

    Parameters
    ----------
    rows:
        ``(abs_time_s, rms_db)`` pairs produced by the envelope extractor.
    gate:
        dB threshold above which a frame counts as "active".
    min_spacing:
        Minimum gap between consecutive detected onsets (s).
    """
    out: list[float] = []
    prev_above = False
    for t, db in rows:
        above = db > gate
        if above and not prev_above:
            if not out or (t - out[-1]) >= min_spacing:
                out.append(t)
        prev_above = above
    return out


def filter_onsets(
    onsets: list[float],
    window_start: float,
    edge_margin: float = 0.15,
    bass_gap: float = 0.30,
) -> list[float]:
    """Drop spurious onsets at the edges and tail of the click train.

    Two passes:

    1. **Edge artifacts** — spikes within ``edge_margin`` of window_start
       (t=0 DC / fade-in block registers as a false onset) are removed.
    2. **Trailing bass-onset bleed** — while the last two onsets are closer
       than ``bass_gap`` apart, the last is popped.  The bass note that ends
       the count-in appears as a tight cluster right at the window end; real
       count beats are spaced > 0.3 s.

    Returns a new (possibly shorter) list; the input is not mutated.
    """
    filtered = [t for t in onsets if t > window_start + edge_margin]
    while len(filtered) >= 2 and (filtered[-1] - filtered[-2]) < bass_gap:
        filtered.pop()
    return filtered


def is_countin(onsets: list[float], min_clicks: int = 5) -> bool:
    """Return True when ``onsets`` looks like a valid count-in.

    Requires at least ``min_clicks`` clicks whose inter-onset gaps are all
    >= 0.30 s (after :func:`filter_onsets` has removed trailing bleed).
    """
    if len(onsets) < min_clicks:
        return False
    for i in range(1, len(onsets)):
        if onsets[i] - onsets[i - 1] < 0.30:
            return False
    return True


def project_cutoff(
    onsets: list[float],
    durations: list[float],
    window_start: float,
    window_end: float,
    n_first: int = 5,
) -> float:
    """Compute cutoff = last_onset + mean(first ``n_first`` durations).

    Clamps the result to ``[window_start, window_end]`` so we never extend
    beyond the detected silence boundary and never set a cutoff before the
    window start.
    """
    if not onsets or not durations:
        return window_end
    first_n = durations[:n_first]
    avg = sum(first_n) / len(first_n)
    cutoff = onsets[-1] + avg
    return max(window_start, min(window_end, cutoff))


# ---------------------------------------------------------------------------
# ffmpeg I/O helpers (not unit-tested; exercised via UAT on real tracks)
# ---------------------------------------------------------------------------


def _envelope(
    src: str | Path,
    start: float,
    dur: float,
    hp: int | None = None,
    mono: bool = False,
) -> list[tuple[float, float]]:
    """Return ``(abs_time_s, rms_db)`` pairs at 5-ms resolution.

    Uses ``asetnsamples + astats + ametadata`` to extract per-frame RMS level.
    Writes metadata to a temp file to avoid stdout/stderr mixing issues.
    """
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as tf:
        out_path = tf.name

    af_parts: list[str] = []
    if mono:
        af_parts.append("pan=mono|c0=0.5*c0+0.5*c1")
    if hp is not None:
        af_parts.append(f"highpass=f={hp}")
    af_parts.append(f"asetnsamples=n={_N_SAMPLES}")
    af_parts.append("astats=metadata=1:reset=1")
    af_parts.append(f"ametadata=print:key=lavfi.astats.Overall.RMS_level:file={out_path}")
    af = ",".join(af_parts)

    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-ss",
            str(start),
            "-t",
            str(dur),
            "-i",
            str(src),
            "-af",
            af,
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        check=False,
    )

    rows: list[tuple[float, float]] = []
    t: float | None = None
    try:
        with open(out_path) as fh:
            for line in fh:
                m = re.search(r"pts_time:([0-9.]+)", line)
                if m:
                    t = float(m.group(1))
                m = re.search(r"RMS_level=(-?[0-9.]+|-inf)", line)
                if m and t is not None:
                    v = m.group(1)
                    db = -120.0 if v == "-inf" else float(v)
                    rows.append((start + t, db))
    except FileNotFoundError:
        pass
    finally:
        Path(out_path).unlink(missing_ok=True)

    return rows


def _measure_duration(
    src: str | Path,
    onset: float,
    gate_db: float = -45.0,
    maxdur: float = 0.4,
) -> float | None:
    """Measure how long the signal stays above gate_db starting near onset.

    Uses highpass=2000 + mono downmix to isolate the transient stick attack,
    matching how the original mix is probed for click duration.

    Parameters
    ----------
    src:
        Path to the source audio (original mix).
    onset:
        Nominal onset time (s); search begins 20 ms before this.
    gate_db:
        Threshold for "active" (default -45 dB).
    maxdur:
        Maximum search window in seconds (default 0.4).

    Returns duration in seconds, or None if no energy found above the gate.
    """
    rows = _envelope(src, max(0.0, onset - 0.02), maxdur, hp=2000, mono=True)
    started = False
    start_t: float | None = None
    last_above: float | None = None
    for t, db in rows:
        if db > gate_db:
            if not started:
                started = True
                start_t = t
            last_above = t
        elif started and last_above is not None and (t - last_above) > 0.03:
            break
    if start_t is None or last_above is None:
        return None
    return last_above - start_t


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def refine_window_end(
    bass_path: Path,
    original_path: Path,
    window_start: float,
    window_end: float,
    margin_db: float = 12.0,
) -> float:
    """Return a refined window end sitting just after the count-in, before the downbeat.

    Algorithm:

    1. Extract the bass-track envelope in ``[window_start, window_end]``.
    2. Compute adaptive gate = median(frame_dbs) + ``margin_db``.
    3. Locate click onsets (spikes above gate, min spacing 0.15 s).
    4. Filter: drop edge artifacts (< 0.15 s from window_start) and trailing
       bass-onset bleed (pop last while last two onsets < 0.30 s apart).
    5. Validate: require >= 5 clicks, all gaps >= 0.30 s.
    6. Measure each click's duration on ``original_path`` (hp 2 kHz, mono,
       gate -45 dB, max 0.4 s search).
    7. Project cutoff = last_click_onset + mean(first_5_durations).
    8. Clamp to ``[window_start, window_end]`` and return.

    Falls back to ``window_end`` if no valid count-in is detected.
    """
    dur = window_end - window_start
    if dur <= 0:
        return window_end

    # Step 1: bass envelope over the window
    rows = _envelope(bass_path, window_start, dur)
    if not rows:
        return window_end

    # Step 2: adaptive gate
    frame_dbs = [db for _, db in rows]
    gate = adaptive_gate(frame_dbs, margin=margin_db)

    # Step 3: locate onsets
    raw_onsets = spikes_from_envelope(rows, gate, min_spacing=0.15)

    # Step 4: filter artifacts
    onsets = filter_onsets(raw_onsets, window_start)

    # Step 5: validate
    if not is_countin(onsets):
        print(
            f"countin: window [{window_start:.3f},{window_end:.3f}] "
            f"{len(onsets)} clicks (not a count-in) -> fallback"
        )
        return window_end

    # Step 6: measure durations on original
    durations: list[float] = []
    for onset in onsets:
        d = _measure_duration(original_path, onset)
        if d is not None:
            durations.append(d)

    if not durations:
        print(
            f"countin: window [{window_start:.3f},{window_end:.3f}] "
            "no measurable durations on original -> fallback"
        )
        return window_end

    # Steps 7 & 8: project and clamp
    cutoff = project_cutoff(onsets, durations, window_start, window_end)
    gaps = [f"{onsets[i + 1] - onsets[i]:.2f}" for i in range(len(onsets) - 1)]
    avg5 = sum(durations[:5]) / len(durations[:5])
    print(
        f"countin: window [{window_start:.3f},{window_end:.3f}] "
        f"{len(onsets)} clicks gaps=[{','.join(gaps)}] "
        f"avg5={avg5 * 1000:.0f}ms last={onsets[-1]:.3f} CUTOFF={cutoff:.3f} "
        f"(delta {(cutoff - window_end) * 1000:+.0f}ms)"
    )
    return cutoff
