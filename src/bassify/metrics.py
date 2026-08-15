from __future__ import annotations

import json
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfiltfilt


def _masked_bandpass_rms(
    y: np.ndarray, mask: np.ndarray, sr: int, low: float | None, high: float | None
) -> float:
    """RMS of y restricted to [low, high) Hz, computed only over samples
    where mask is True. Either bound may be None for an open-ended
    highpass/lowpass. Zero-phase (sosfiltfilt) to avoid ringing artifacts
    skewing the RMS.

    Filters the FULL signal first, then applies the mask -- filtering AFTER
    masking would run the filter over a discontinuous concatenation of
    non-adjacent samples at each mask boundary (boolean fancy-indexing
    joins samples that weren't neighbors in real time), injecting spurious
    high-band energy from the resulting step discontinuities. Filtering the
    full contiguous signal first avoids that entirely.
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
    masked = filtered[mask]
    if len(masked) == 0:
        return 0.0
    return float(np.sqrt(np.mean(masked**2)))


def _music_mask(n: int, sr: int, windows_path: Path) -> np.ndarray:
    """Boolean mask, True where active music plays -- everywhere outside the
    detected silence windows (count-in, narration gaps).
    """
    windows = json.loads(Path(windows_path).read_text())
    silent_mask = np.zeros(n, dtype=bool)
    for w in windows:
        start_sample = int(w["start"] * sr)
        end_sample = min(int(w["end"] * sr), n)
        silent_mask[start_sample:end_sample] = True
    return ~silent_mask


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

    music_mask = _music_mask(n, sr, windows_path)

    dirty_high = _masked_bandpass_rms(yd, music_mask, sr, low=band_cutoff, high=None)
    clean_high = _masked_bandpass_rms(yc, music_mask, sr, low=band_cutoff, high=None)
    dirty_low = _masked_bandpass_rms(yd, music_mask, sr, low=None, high=band_cutoff)
    clean_low = _masked_bandpass_rms(yc, music_mask, sr, low=None, high=band_cutoff)

    high_delta = 20 * np.log10(max(clean_high, 1e-12) / max(dirty_high, 1e-12))
    low_delta = 20 * np.log10(max(clean_low, 1e-12) / max(dirty_low, 1e-12))
    return float(high_delta), float(low_delta)


def compute_source_referenced_leak_db(
    clean_path: Path,
    original_path: Path,
    windows_path: Path,
    band_cutoff: float = 800.0,
) -> tuple[float, float]:
    """How much non-bass content survives in bass_clean.wav, measured
    against the ORIGINAL track during active music.

    Returns (rejection_db, residual_vs_bass_db):

    - rejection_db: clean high-band energy relative to the ORIGINAL's
      high-band energy. A true rejection ratio -- "what fraction of the
      source's guitar/drums did we remove". A DSP performance number.

    - residual_vs_bass_db: clean high-band energy relative to the
      ORIGINAL's LOW-band (bass) energy -- "how much junk is left per unit
      of the bass we were isolating". An audibility number.

    Both are referenced to the source, because comparing two derived files
    against each other says nothing about how much of the original was
    actually removed. They differ because the original's high-band energy
    varies with how bright and busy the mix is, which has nothing to do
    with cancellation quality: on the real collection, tracks 03 and 06
    leaked within 0.6 dB of each other but scored 7.6 dB apart on
    rejection alone, purely because 06's source is brighter. Referencing
    the bass instead groups tracks the way they sound.

    Lower (more negative) is better for both.
    """
    yc, sr = sf.read(str(clean_path), dtype="float64", always_2d=False)
    yo = librosa.load(str(original_path), sr=sr, mono=True)[0].astype(np.float64)

    n = min(len(yc), len(yo))
    yc, yo = yc[:n], yo[:n]

    music_mask = _music_mask(n, sr, windows_path)

    clean_high = _masked_bandpass_rms(yc, music_mask, sr, low=band_cutoff, high=None)
    original_high = _masked_bandpass_rms(yo, music_mask, sr, low=band_cutoff, high=None)
    original_low = _masked_bandpass_rms(yo, music_mask, sr, low=None, high=band_cutoff)

    rejection = 20 * np.log10(max(clean_high / max(original_high, 1e-12), 1e-12))
    residual = 20 * np.log10(max(clean_high / max(original_low, 1e-12), 1e-12))
    return float(rejection), float(residual)


def scan_collection(
    collection_dir: Path, band_cutoff: float = 800.0
) -> list[tuple[str, float, float, float | None, float | None]]:
    """For each track directory under collection_dir with both a dirty
    bass.wav and a bass_clean.wav, compute (name, high_band_delta_db,
    low_band_delta_db, rejection_db, residual_vs_bass_db).

    rejection_db and residual_vs_bass_db are both None when the original
    source track can't be found at tracks/<collection>/<track_name>.* (e.g.
    a differently-named or missing source file).
    """
    collection_dir = Path(collection_dir)
    tracks_dir = Path("tracks") / collection_dir.name

    rows: list[tuple[str, float, float, float | None, float | None]] = []
    for track_dir in sorted(collection_dir.iterdir()):
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

        original_path = next(tracks_dir.glob(f"{track_dir.name}.*"), None)
        if original_path is not None:
            rejection, residual = compute_source_referenced_leak_db(
                bass_clean_path, original_path, windows_path, band_cutoff=band_cutoff
            )
        else:
            rejection, residual = None, None
        rows.append((track_dir.name, high_delta, low_delta, rejection, residual))
    return rows


def print_report(rows: list[tuple[str, float, float, float | None, float | None]]) -> None:
    print(
        f"{'track':<45} {'high-band Δ (dB)':>16} {'low-band Δ (dB)':>16} "
        f"{'rejection (dB)':>15} {'residual/bass (dB)':>19}"
    )
    for name, high_delta, low_delta, rejection, residual in rows:
        rej_str = f"{rejection:.1f}" if rejection is not None else "n/a"
        res_str = f"{residual:.1f}" if residual is not None else "n/a"
        print(f"{name:<45} {high_delta:>16.1f} {low_delta:>16.1f} {rej_str:>15} {res_str:>19}")
