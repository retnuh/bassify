from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfiltfilt


def _bandpass_rms(y: np.ndarray, sr: int, low: float | None, high: float | None) -> float:
    """RMS of y restricted to [low, high) Hz. Either bound may be None for an
    open-ended highpass/lowpass. Zero-phase (sosfiltfilt) to avoid ringing
    artifacts skewing the RMS.
    """
    if len(y) == 0:
        return 0.0
    if low is None and high is None:
        filtered = y
    elif low is None:
        sos = butter(4, high, btype="lowpass", fs=sr, output="sos")
        filtered = sosfiltfilt(sos, y)
    elif high is None:
        sos = butter(4, low, btype="highpass", fs=sr, output="sos")
        filtered = sosfiltfilt(sos, y)
    else:
        sos = butter(4, [low, high], btype="bandpass", fs=sr, output="sos")
        filtered = sosfiltfilt(sos, y)
    return float(np.sqrt(np.mean(filtered**2)))


def compute_music_band_delta_db(
    dirty_path: Path,
    clean_path: Path,
    windows_path: Path,
    band_cutoff: float = 800.0,
) -> tuple[float, float]:
    """Compare dirty vs clean bass during ACTIVE MUSIC -- everywhere outside
    the detected silence windows, since that's where bleed actually bothers
    a practicing bassist and where the dirty/clean comparison is meaningful.

    Splits into a high band (> band_cutoff Hz, roughly guitar range -- bass
    barely lives there) and a low band (<= band_cutoff Hz, bass range).

    Returns (high_band_delta_db, low_band_delta_db):
    - high_band_delta_db: dB change in high-band energy from dirty to clean.
      More negative = more guitar/leak removed where it actually matters
      (while the bass is playing). This is the primary leak-reduction
      signal.
    - low_band_delta_db: dB change in low-band (bass-range) energy. Should
      stay near 0 -- a large negative value means the projection is
      damaging bass itself, not just cancelling guitar. Read this as a
      safety check on high_band_delta_db, not a leak measurement.

    Silence-window gaps (count-in, narration) are excluded entirely -- they
    contain no bass to protect and no music to compare, so they're outside
    the scope of this metric.
    """
    yd, sr = sf.read(str(dirty_path), dtype="float64", always_2d=False)
    yc, _ = sf.read(str(clean_path), dtype="float64", always_2d=False)
    n = min(len(yd), len(yc))
    yd, yc = yd[:n], yc[:n]

    windows = json.loads(Path(windows_path).read_text())
    silent_mask = np.zeros(n, dtype=bool)
    for w in windows:
        start_sample = int(w["start"] * sr)
        end_sample = min(int(w["end"] * sr), n)
        silent_mask[start_sample:end_sample] = True
    music_mask = ~silent_mask

    yd_music = yd[music_mask]
    yc_music = yc[music_mask]

    dirty_high = _bandpass_rms(yd_music, sr, low=band_cutoff, high=None)
    clean_high = _bandpass_rms(yc_music, sr, low=band_cutoff, high=None)
    dirty_low = _bandpass_rms(yd_music, sr, low=None, high=band_cutoff)
    clean_low = _bandpass_rms(yc_music, sr, low=None, high=band_cutoff)

    high_delta = 20 * np.log10(max(clean_high, 1e-12) / max(dirty_high, 1e-12))
    low_delta = 20 * np.log10(max(clean_low, 1e-12) / max(dirty_low, 1e-12))
    return float(high_delta), float(low_delta)


def scan_collection(collection_dir: Path, band_cutoff: float = 800.0) -> list[tuple[str, float, float]]:
    """For each track directory under collection_dir with both a dirty
    bass.wav and a bass_clean.wav, compute (name, high_band_delta_db,
    low_band_delta_db) via compute_music_band_delta_db.
    """
    rows: list[tuple[str, float, float]] = []
    for track_dir in sorted(Path(collection_dir).iterdir()):
        if not track_dir.is_dir():
            continue
        windows_path = next(track_dir.glob("*_silence_windows*.json"), None)
        bass_path = next(
            (p for p in track_dir.glob("*_bass.wav") if "_bass_clean" not in p.name), None
        )
        bass_clean_path = next(track_dir.glob("*_bass_clean.wav"), None)
        if windows_path is None or bass_path is None or bass_clean_path is None:
            continue
        high_delta, low_delta = compute_music_band_delta_db(
            bass_path, bass_clean_path, windows_path, band_cutoff=band_cutoff
        )
        rows.append((track_dir.name, high_delta, low_delta))
    return rows


def print_report(rows: list[tuple[str, float, float]]) -> None:
    print(f"{'track':<45} {'high-band Δ (dB)':>16} {'low-band Δ (dB)':>16}")
    for name, high_delta, low_delta in rows:
        print(f"{name:<45} {high_delta:>16.1f} {low_delta:>16.1f}")
