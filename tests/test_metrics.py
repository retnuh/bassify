from __future__ import annotations

import json

import numpy as np
import soundfile as sf

from bassify.metrics import compute_absolute_leak_db, compute_music_band_delta_db


def _write_windows(tmp_path, windows):
    p = tmp_path / "windows.json"
    p.write_text(json.dumps(windows))
    return p


def test_high_band_delta_strongly_negative_when_guitar_removed(tmp_path):
    """dirty = bass + guitar, clean = bass only (guitar fully removed).

    The high band (>800Hz, roughly guitar range) should show a large drop
    from dirty to clean; the low band (bass range) should stay near 0dB,
    since the bass content itself is untouched.
    """
    sr = 8000
    n = sr * 4
    t = np.arange(n) / sr
    bass = 0.3 * np.sin(2 * np.pi * 80 * t)
    guitar = 0.3 * np.sin(2 * np.pi * 2000 * t)

    dirty = bass + guitar
    clean = bass

    dirty_path = tmp_path / "bass.wav"
    clean_path = tmp_path / "bass_clean.wav"
    sf.write(str(dirty_path), dirty, sr, subtype="PCM_24")
    sf.write(str(clean_path), clean, sr, subtype="PCM_24")

    # First 1s is a count-in window, excluded from scoring.
    windows_path = _write_windows(tmp_path, [{"start": 0.0, "end": 1.0}])

    high_delta, low_delta = compute_music_band_delta_db(dirty_path, clean_path, windows_path)

    assert high_delta < -15  # guitar removed -> big drop in the high band
    assert abs(low_delta) < 3  # bass preserved -> low band roughly unchanged


def test_deltas_near_zero_when_clean_matches_dirty(tmp_path):
    """dirty == clean (no cancellation applied) -> both band deltas ~0dB."""
    sr = 8000
    n = sr * 4
    t = np.arange(n) / sr
    y = 0.3 * np.sin(2 * np.pi * 80 * t) + 0.3 * np.sin(2 * np.pi * 2000 * t)

    dirty_path = tmp_path / "bass2.wav"
    clean_path = tmp_path / "bass2_clean.wav"
    sf.write(str(dirty_path), y, sr, subtype="PCM_24")
    sf.write(str(clean_path), y, sr, subtype="PCM_24")

    windows_path = _write_windows(tmp_path, [{"start": 0.0, "end": 1.0}])

    high_delta, low_delta = compute_music_band_delta_db(dirty_path, clean_path, windows_path)

    assert abs(high_delta) < 1
    assert abs(low_delta) < 1


def test_absolute_leak_reflects_how_much_original_high_band_survives(tmp_path):
    """Absolute leak score should distinguish 'fully cleaned' from
    'partially cleaned' relative to the ORIGINAL track, independent of any
    naive-baseline comparison.
    """
    sr = 8000
    n = sr * 4
    t = np.arange(n) / sr
    bass = 0.3 * np.sin(2 * np.pi * 80 * t)
    guitar = 0.3 * np.sin(2 * np.pi * 2000 * t)

    original_path = tmp_path / "original.wav"
    sf.write(str(original_path), bass + guitar, sr, subtype="PCM_24")

    windows_path = _write_windows(tmp_path, [{"start": 0.0, "end": 1.0}])

    clean_full_path = tmp_path / "bass_clean_full.wav"
    sf.write(str(clean_full_path), bass, sr, subtype="PCM_24")  # guitar fully removed

    clean_partial_path = tmp_path / "bass_clean_partial.wav"
    sf.write(str(clean_partial_path), bass + 0.5 * guitar, sr, subtype="PCM_24")  # half removed

    leak_full = compute_absolute_leak_db(clean_full_path, original_path, windows_path)
    leak_partial = compute_absolute_leak_db(clean_partial_path, original_path, windows_path)

    assert leak_full < -15  # fully cleaned -> most of original's high-band gone
    assert leak_partial > leak_full  # partial cleanup leaves more, so a less negative score
    assert leak_partial < -1  # still some reduction happened
