"""Frame-level leak metric with a fit/score holdout, and a static-vs-time-varying
projection comparison (the deferred "Approach B").

Scores every bass-free STFT frame individually instead of requiring contiguous
gaps, so tracks without long mid-song rests still yield thousands of
measurement frames. Bass-free frames are chosen from the SOURCE track by an
absolute threshold (not a percentile), so a track with continuous bass honestly
reports "no score" instead of scoring its quietest bass notes as leak.

The bass-free frames are split alternately into a fit half and a score half:
gains are fitted on the fit half only, so the reported leak is measured on
frames the projection never saw.

Run: uv run python experiments/frame_leak.py "43_Sweet Home Chicago" ...
"""

import sys
from pathlib import Path

import librosa
import numpy as np
from scipy.signal import stft

sys.path.insert(0, "src")
from bassify.extract import (  # noqa: E402
    STFT_HOP,
    STFT_NOVERLAP,
    STFT_NPERSEG,
    apply_fractional_delay,
    estimate_delay,
    fit_projection_gains,
)

COLLECTION = "BluesBass"
LOW_CUTOFF_HZ = 250.0
# A frame is bass-free when its low-band energy is this far below the track's
# own loud-frame level. Absolute, so it can select nothing.
BASS_FREE_DROP_DB = 30.0
LOUD_PERCENTILE = 90.0
MIN_SCORE_FRAMES = 100

BLOCK_SECONDS = 10.0
MIN_BLOCK_FIT_FRAMES = 20


def frame_energy(Z: np.ndarray) -> np.ndarray:
    return np.mean(np.abs(Z) ** 2, axis=0)


def db_ratio(num: float, den: float) -> float:
    if num <= 0 or den <= 0:
        return float("nan")
    return 10 * np.log10(num / den)


def leak_db(
    Z: np.ndarray,
    score_idx: np.ndarray,
    signal_idx: np.ndarray,
    bins: np.ndarray | None = None,
) -> float:
    e = frame_energy(Z if bins is None else Z[bins, :])
    return db_ratio(float(np.mean(e[score_idx])), float(np.mean(e[signal_idx])))


def fit_time_varying(
    L_stft: np.ndarray,
    R_stft: np.ndarray,
    fit_idx: np.ndarray,
    sr: int,
    global_h: np.ndarray,
) -> np.ndarray:
    """Per-block complex gains: H[k] refitted every BLOCK_SECONDS from the fit
    frames inside that block, falling back to the global fit when a block has
    too few. Returns an (n_bins, n_frames) gain array.
    """
    n_frames = L_stft.shape[1]
    block_frames = max(1, int(BLOCK_SECONDS * sr / STFT_HOP))
    H = np.repeat(global_h[:, None], n_frames, axis=1)
    refit = total = 0

    for start in range(0, n_frames, block_frames):
        end = min(start + block_frames, n_frames)
        total += 1
        local = fit_idx[(fit_idx >= start) & (fit_idx < end)]
        if len(local) < MIN_BLOCK_FIT_FRAMES:
            continue
        local_mask = np.zeros(n_frames, dtype=bool)
        local_mask[local] = True
        try:
            h = fit_projection_gains(
                L_stft, R_stft, local_mask, sr=sr, hop_length=STFT_HOP, min_seconds=0.0
            )
        except Exception:
            continue
        H[:, start:end] = h[:, None]
        refit += 1
    print(f"    time-varying: {refit}/{total} blocks refitted, rest used global fit")
    return H


def fit_interpolated(
    L_stft: np.ndarray,
    R_stft: np.ndarray,
    fit_idx: np.ndarray,
    all_bass_free: np.ndarray,
    sr: int,
    global_h: np.ndarray,
    window_seconds: float = 15.0,
    min_run: int = 5,
) -> np.ndarray:
    """Time-varying gains anchored where bass-free frames actually occur.

    Fixed blocks starve because bass-free frames clump. Instead: find runs of
    bass-free fit frames, fit H[k] at each run's centre using every fit frame
    within +/- window_seconds of it, then interpolate the gains across time
    between anchors. Complex gains are interpolated in magnitude and unwrapped
    phase separately -- interpolating real/imag directly would pull magnitude
    toward zero wherever two anchors differ in phase.
    """
    n_frames = L_stft.shape[1]
    if len(fit_idx) == 0:
        return np.repeat(global_h[:, None], n_frames, axis=1)

    # Runs come from every bass-free frame; only the fitting below is
    # restricted to the holdout half, so anchors land on real rests.
    runs, start = [], all_bass_free[0]
    for prev, cur in zip(all_bass_free[:-1], all_bass_free[1:], strict=False):
        if cur != prev + 1:
            runs.append((start, prev))
            start = cur
    runs.append((start, all_bass_free[-1]))
    anchors = [(a + b) // 2 for a, b in runs if b - a + 1 >= min_run]

    if len(anchors) < 2:
        print(f"    interpolated: only {len(anchors)} anchor(s), falling back to global")
        return np.repeat(global_h[:, None], n_frames, axis=1)

    half = int(window_seconds * sr / STFT_HOP)
    fitted = []
    for centre in anchors:
        local = fit_idx[np.abs(fit_idx - centre) <= half]
        m = np.zeros(n_frames, dtype=bool)
        m[local] = True
        try:
            fitted.append(
                fit_projection_gains(L_stft, R_stft, m, sr=sr, hop_length=STFT_HOP, min_seconds=0.0)
            )
        except Exception:
            fitted.append(global_h)
    print(f"    interpolated: {len(anchors)} anchors over {n_frames} frames")

    A = np.array(anchors)
    G = np.stack(fitted, axis=1)  # (bins, anchors)
    frames = np.arange(n_frames)
    mag = np.stack([np.interp(frames, A, np.abs(G[k])) for k in range(G.shape[0])])
    phase = np.stack([np.interp(frames, A, np.unwrap(np.angle(G[k]))) for k in range(G.shape[0])])
    return mag * np.exp(1j * phase)


def analyse(track: str) -> None:
    orig = next((Path("tracks") / COLLECTION).glob(f"{track}.*"))
    y, sr = librosa.load(str(orig), sr=None, mono=False)
    if y.ndim == 1:
        print(f"{track}: mono source, skipping")
        return
    L, R = y[0].astype(np.float64), y[1].astype(np.float64)

    delay = estimate_delay(L, R, sr)
    R = apply_fractional_delay(R, -delay)

    freqs, _, L_stft = stft(L, fs=sr, nperseg=STFT_NPERSEG, noverlap=STFT_NOVERLAP)
    _, _, R_stft = stft(R, fs=sr, nperseg=STFT_NPERSEG, noverlap=STFT_NOVERLAP)

    low = np.sum(np.abs(L_stft[freqs < LOW_CUTOFF_HZ, :]) ** 2, axis=0)
    loud = np.percentile(low, LOUD_PERCENTILE)
    bass_free = np.flatnonzero(low <= loud * 10 ** (-BASS_FREE_DROP_DB / 10))
    signal_idx = np.flatnonzero(low >= loud * 10 ** (-6.0 / 10))

    n_frames = L_stft.shape[1]
    pct = 100.0 * len(bass_free) / n_frames
    print(f"\n=== {track}")
    print(
        f"    {len(bass_free)} bass-free frames of {n_frames} ({pct:.1f}%), "
        f"{len(signal_idx)} bass-present"
    )
    # Are the bass-free frames spread across the track, or bunched at the
    # quiet intro/outro? A score built from edge frames says nothing about the
    # body of the track.
    deciles = np.histogram(bass_free, bins=10, range=(0, n_frames))[0]
    print("    bass-free frames per decile: " + " ".join(f"{c:4d}" for c in deciles))

    if len(bass_free) < 2 * MIN_SCORE_FRAMES or len(signal_idx) < MIN_SCORE_FRAMES:
        print("    not enough bass-free content to score -- no number reported")
        return

    fit_idx, score_idx = bass_free[0::2], bass_free[1::2]
    fit_mask = np.zeros(n_frames, dtype=bool)
    fit_mask[fit_idx] = True

    naive = L_stft - R_stft
    h = fit_projection_gains(L_stft, R_stft, fit_mask, sr=sr, hop_length=STFT_HOP)
    static = L_stft - h[:, None] * R_stft
    H_tv = fit_time_varying(L_stft, R_stft, fit_idx, sr, h)
    varying = L_stft - H_tv * R_stft

    # Only content below the 800 Hz backstop survives into bass_clean.wav, so
    # a win measured over the full spectrum can be entirely in bands that get
    # filtered away anyway. Report the surviving band separately.
    inband = freqs < 800.0
    print("    band          full   <800Hz (the only band that survives)")
    for name, Z in (("naive L-R", naive), ("static proj", static), ("interp B'", None)):
        if Z is None:
            continue
        print(
            f"    {name:12s} {leak_db(Z, score_idx, signal_idx):6.1f}  "
            f"{leak_db(Z, score_idx, signal_idx, inband):6.1f}"
        )

    print(f"    naive L-R          leak {leak_db(naive, score_idx, signal_idx):6.1f} dB")
    print(f"    static projection  leak {leak_db(static, score_idx, signal_idx):6.1f} dB")
    print(f"    time-varying (B)   leak {leak_db(varying, score_idx, signal_idx):6.1f} dB")

    H_interp = fit_interpolated(L_stft, R_stft, fit_idx, bass_free, sr, h)
    interp = L_stft - H_interp * R_stft
    print(f"    interpolated (B')  leak {leak_db(interp, score_idx, signal_idx):6.1f} dB")

    # How much could ANY linear projection of R onto L remove? Per bin, the
    # best possible residual fraction over bass-free frames is 1 - coherence.
    # Content that is decorrelated between channels (reverb tails, stereo
    # widening) sits above this floor and no choice of H[k] -- static or
    # time-varying -- can touch it.
    Lb, Rb = L_stft[:, bass_free], R_stft[:, bass_free]
    cross = np.abs(np.sum(Lb * np.conj(Rb), axis=1)) ** 2
    auto = np.sum(np.abs(Lb) ** 2, axis=1) * np.sum(np.abs(Rb) ** 2, axis=1)
    coh = np.divide(cross, auto, out=np.zeros_like(cross), where=auto > 0)
    l_energy = np.sum(np.abs(Lb) ** 2, axis=1)
    floor = db_ratio(float(np.sum(l_energy * (1 - coh))), float(np.sum(l_energy)))
    print(f"    linear-cancellation ceiling {floor:6.1f} dB (energy-weighted 1-coherence)")

    # Does leak drift over the track? High spread is the evidence that one
    # static H[k] cannot fit the whole track.
    block_frames = max(1, int(BLOCK_SECONDS * sr / STFT_HOP))
    per_block = []
    for start in range(0, n_frames, block_frames):
        end = min(start + block_frames, n_frames)
        s = score_idx[(score_idx >= start) & (score_idx < end)]
        g = signal_idx[(signal_idx >= start) & (signal_idx < end)]
        if len(s) < 10 or len(g) < 10:
            continue
        per_block.append(leak_db(static, s, g))
    if per_block:
        arr = np.array(per_block)
        print(
            f"    static leak per {BLOCK_SECONDS:.0f}s block: "
            f"min {arr.min():.1f}  median {np.median(arr):.1f}  max {arr.max():.1f}  "
            f"spread {arr.max() - arr.min():.1f} dB  (n={len(arr)})"
        )


def main() -> None:
    tracks = sys.argv[1:]
    if not tracks:
        sys.exit("usage: frame_leak.py <track dir name> ...")
    for t in tracks:
        analyse(t)


if __name__ == "__main__":
    main()
