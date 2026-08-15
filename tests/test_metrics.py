from __future__ import annotations

import json

import numpy as np
import soundfile as sf

from bassify.metrics import compute_residual_db


def test_compute_residual_db_lower_when_windows_quieter(tmp_path):
    sr = 8000
    n = sr * 4
    y = np.zeros(n)
    y[: n // 2] = 0.5  # bass-active region, loud
    y[n // 2 :] = 0.05  # bass-silent window, quiet residual

    bass_path = tmp_path / "bass.wav"
    sf.write(str(bass_path), y, sr, subtype="PCM_24")

    windows = [{"start": (n // 2) / sr, "end": n / sr}]
    windows_path = tmp_path / "windows.json"
    windows_path.write_text(json.dumps(windows))

    db = compute_residual_db(bass_path, windows_path)
    assert db < -10


def test_compute_residual_db_higher_when_windows_leak_loudly(tmp_path):
    sr = 8000
    n = sr * 4
    y = np.zeros(n)
    y[: n // 2] = 0.5
    y[n // 2 :] = 0.4  # loud leak, close to the bass level

    bass_path = tmp_path / "bass2.wav"
    sf.write(str(bass_path), y, sr, subtype="PCM_24")

    windows = [{"start": (n // 2) / sr, "end": n / sr}]
    windows_path = tmp_path / "windows2.json"
    windows_path.write_text(json.dumps(windows))

    db = compute_residual_db(bass_path, windows_path)
    assert db > -5
