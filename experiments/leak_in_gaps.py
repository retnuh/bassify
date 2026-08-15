"""Measure guitar leak in genuinely bass-free stretches of a track.

Where the source track has no bass at all (e.g. the guitar-only passages in
43_Sweet Home Chicago after ~30s), anything left in bass_clean.wav is pure
leak -- an absolute measure of cancellation quality that needs no band
splitting and no naive-baseline comparison.

Prints the detected bass-free intervals plus, for both bass.wav and
bass_clean.wav, the leak-to-signal ratio: RMS during bass-free stretches vs
RMS during bass-present stretches. Lower (more negative dB) is better.
"""

import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import stft

sys.path.insert(0, "src")
from bassify.extract import STFT_HOP, STFT_NPERSEG  # noqa: E402

COLLECTION = "BluesBass"
MIN_GAP_SECONDS = 0.5


def frame_mask_from_source(orig_path: Path, sr_target: int) -> tuple[np.ndarray, int]:
    """Bass-free frame mask from the source track's own low-band energy.

    Deliberately not reusing extract.bass_free_frame_mask's percentile default:
    here we want stretches that are bass-free in absolute terms, so the
    threshold is set relative to the track's median low-band energy.
    """
    import librosa

    y, sr = librosa.load(str(orig_path), sr=sr_target, mono=True)
    freqs, _, Z = stft(y, fs=sr, nperseg=STFT_NPERSEG, noverlap=STFT_NPERSEG - STFT_HOP)
    low = np.sum(np.abs(Z[freqs < 200.0, :]) ** 2, axis=0)
    quiet = low <= np.percentile(low, 20)
    return quiet, sr


def intervals(mask: np.ndarray, sr: int, min_seconds: float) -> list[tuple[float, float]]:
    out = []
    start = None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(mask)))
    secs = [(a * STFT_HOP / sr, b * STFT_HOP / sr) for a, b in out]
    return [(a, b) for a, b in secs if b - a >= min_seconds]


def sample_mask(gaps: list[tuple[float, float]], n: int, sr: int) -> np.ndarray:
    m = np.zeros(n, dtype=bool)
    for a, b in gaps:
        m[int(a * sr) : min(int(b * sr), n)] = True
    return m


def rms(y: np.ndarray, m: np.ndarray) -> float:
    v = y[m]
    return float(np.sqrt(np.mean(v**2))) if v.size else 0.0


def db(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return float("-inf")
    return 20 * np.log10(a / b)


def main() -> None:
    for track in sys.argv[1:]:
        d = Path("out") / COLLECTION / track
        clean_p = d / f"{track}_bass_clean.wav"
        y_clean, sr = sf.read(str(clean_p), dtype="float64", always_2d=True)
        y_clean = y_clean.mean(axis=1)

        orig = next(
            Path("tracks") / COLLECTION and (Path("tracks") / COLLECTION).glob(f"{track}.*")
        )
        quiet, _ = frame_mask_from_source(orig, sr)
        gaps = intervals(quiet, sr, MIN_GAP_SECONDS)

        print(f"\n=== {track} — {len(gaps)} bass-free stretches >= {MIN_GAP_SECONDS}s")
        for a, b in gaps[:20]:
            print(f"    {a:7.2f} - {b:7.2f}  ({b - a:.2f}s)")

        gap_m = sample_mask(gaps, len(y_clean), sr)
        play_m = ~gap_m
        for kind in ("bass", "bass_clean"):
            p = d / f"{track}_{kind}.wav"
            y, _ = sf.read(str(p), dtype="float64", always_2d=True)
            y = y.mean(axis=1)
            n = min(len(y), len(gap_m))
            ratio = db(rms(y[:n], gap_m[:n]), rms(y[:n], play_m[:n]))
            print(f"    {kind:11s} leak-to-signal {ratio:6.1f} dB")

        # Does the steeper "C" backstop (12-pole @800) move this metric at all?
        from scipy.signal import butter, sosfilt

        sos = butter(2, 800.0 / (sr / 2), btype="low", output="sos")
        z = y_clean
        for _ in range(4):
            z = sosfilt(sos, z)
        n = min(len(z), len(gap_m))
        print(
            f"    {'clean+C':11s} leak-to-signal "
            f"{db(rms(z[:n], gap_m[:n]), rms(z[:n], play_m[:n])):6.1f} dB"
        )


if __name__ == "__main__":
    main()
