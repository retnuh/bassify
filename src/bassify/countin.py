"""Count-in click cutoff refinement using librosa onset detection.

Guitar onsets appear ONLY in the original (guitar cancels in L-R bass extraction),
while click/beat onsets appear in BOTH bass and original. The cutoff is placed just
before the first guitar-only onset (unmatched in bass) after the last matched click.

Algorithm (per experiments/guitar_onset.py):
1. Load bass + original audio once (full file), slice per window.
2. Compute onsets for [window_start, window_end] on each track.
3. Match bass onsets to original onsets within 0.05s tolerance.
4. Matched = clicks; unmatched = guitar/speech candidates.
5. Find first unmatched onset AFTER last_click + 0.01s.
6. cutoff = guitar - 0.02 (MARGIN), clamped to [window_start, window_end].
7. Falls back to window_end when no guitar onset found.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Pure functions — unit-testable without librosa or ffmpeg
# ---------------------------------------------------------------------------


def match_onsets(
    bass_onsets: list[float],
    orig_onsets: list[float],
    tol: float = 0.05,
) -> tuple[list[float], list[float]]:
    """Split orig_onsets into matched (have a nearby bass onset) and unmatched.

    Parameters
    ----------
    bass_onsets:
        Onset times from the bass track (clicks appear here).
    orig_onsets:
        Onset times from the original mix.
    tol:
        Maximum distance (s) to count as a match.

    Returns
    -------
    (matched, unmatched) — two disjoint sublists of orig_onsets preserving order.
    """
    matched: list[float] = []
    unmatched: list[float] = []
    for t in orig_onsets:
        if any(abs(t - bt) < tol for bt in bass_onsets):
            matched.append(t)
        else:
            unmatched.append(t)
    return matched, unmatched


def find_guitar_cutoff(
    bass_onsets: list[float],
    orig_onsets: list[float],
    window_start: float,
    window_end: float,
    tol: float = 0.05,
    margin: float = 0.005,
) -> float:
    """Return the cutoff time just before the first guitar-only onset.

    This is the core testable logic (no I/O). Takes pre-computed onset lists
    and returns the cutoff to use as the gate end.

    Parameters
    ----------
    bass_onsets:
        Onsets from the bass track within the window.
    orig_onsets:
        Onsets from the original track within the window (already filtered to < we-0.02).
    window_start:
        Start of the silence window (s).
    window_end:
        End of the silence window / music onset (s).
    tol:
        Match tolerance (s); bass onset within this of an orig onset = same event.
    margin:
        Seconds before the guitar onset where the cutoff is placed.

    Returns
    -------
    Cutoff in seconds, clamped to [window_start, window_end].
    """
    matched, unmatched = match_onsets(bass_onsets, orig_onsets, tol=tol)

    # last_click: last matched (click) onset; fallback to last bass onset or window_start
    if matched:
        last_click = matched[-1]
    elif bass_onsets:
        last_click = bass_onsets[-1]
    else:
        last_click = window_start

    # guitar: first unmatched orig onset strictly after last_click + 0.01s
    guitar = next((t for t in unmatched if t > last_click + 0.01), None)

    if guitar is not None:
        cutoff = guitar - margin
        cutoff = max(window_start, min(window_end, cutoff))
        print(
            f"countin: [{window_start:.3f},{window_end:.3f}] "
            f"last_click={last_click:.3f} guitar={guitar:.3f} "
            f"CUTOFF={cutoff:.3f}"
        )
        return cutoff

    # No guitar-only onset found: fall back to last_click + 0.10 (capped at window_end)
    cutoff = min(window_end, last_click + 0.10)
    print(
        f"countin: [{window_start:.3f},{window_end:.3f}] "
        f"last_click={last_click:.3f} NO guitar onset -> fallback={cutoff:.3f}"
    )
    return cutoff


# ---------------------------------------------------------------------------
# librosa I/O layer — not unit-tested; exercised via UAT on real tracks
# ---------------------------------------------------------------------------

# Module-level audio cache: path -> (y, sr) per loaded file
_audio_cache: dict[str, tuple[object, int]] = {}


def _load_cached(path: str | Path) -> tuple[object, int]:
    """Load audio file once; return (y, sr) from cache on subsequent calls."""
    import librosa

    key = str(path)
    if key not in _audio_cache:
        y, sr = librosa.load(str(path), sr=None, mono=True)
        _audio_cache[key] = (y, sr)
    return _audio_cache[key]


def _onsets_in_window(
    y: object,
    sr: int,
    window_start: float,
    window_end: float,
) -> list[float]:
    """Return onset times (absolute, in seconds) within [window_start, window_end].

    Slices the pre-loaded array, computes onset strength, detects onsets with
    backtrack=True, and converts frame indices back to absolute timestamps.
    """
    import librosa

    y_arr = y  # already a numpy array from librosa.load
    ws_samp = int(window_start * sr)
    we_samp = int(window_end * sr)
    seg = y_arr[ws_samp:we_samp]

    if seg.size == 0:
        return []

    env = librosa.onset.onset_strength(y=seg, sr=sr)
    frames = librosa.onset.onset_detect(
        onset_envelope=env,
        sr=sr,
        backtrack=True,
        units="frames",
    )
    return [float(librosa.frames_to_time(f, sr=sr)) + window_start for f in frames]


def refine_window(
    bass_path: Path,
    original_path: Path,
    window_start: float,
    window_end: float,
) -> float:
    """Return refined cutoff for a window using librosa guitar-onset detection.

    Public API. Loads bass and original audio (cached per path), computes
    onsets in [window_start, window_end] for each, then delegates to the
    pure find_guitar_cutoff() function.

    Parameters
    ----------
    bass_path:
        Path to the bass (L-R) audio file.
    original_path:
        Path to the original stereo mix.
    window_start:
        Start of the silence window (s).
    window_end:
        End of the silence window / detected music onset (s).

    Returns
    -------
    Cutoff timestamp in seconds.
    """
    if window_end <= window_start:
        return window_end

    yb, sr_b = _load_cached(bass_path)
    yo, sr_o = _load_cached(original_path)

    bass_onsets = _onsets_in_window(yb, sr_b, window_start, window_end)

    # Original onsets filtered to < we - 0.02 (per reference algorithm)
    raw_orig = _onsets_in_window(yo, sr_o, window_start, window_end)
    orig_onsets = [t for t in raw_orig if t < window_end - 0.02]

    return find_guitar_cutoff(
        bass_onsets,
        orig_onsets,
        window_start,
        window_end,
    )


# ---------------------------------------------------------------------------
# Backward-compat alias (detect.py previously called refine_window_end)
# ---------------------------------------------------------------------------

refine_window_end = refine_window
