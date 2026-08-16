from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from bassify.render.tempo import _trim_silence, detect_bpm

SR = 22050


def _click_train(bpm: float, duration: float, sr: int = SR) -> np.ndarray:
    """A short decaying click every beat -- enough transient content for
    librosa's onset-strength envelope to lock onto, without needing real audio."""
    period = 60.0 / bpm
    n = int(duration * sr)
    y = np.zeros(n)
    click_len = int(0.02 * sr)
    t = 0.0
    while t < duration:
        i = int(t * sr)
        end = min(i + click_len, n)
        env = np.exp(-np.linspace(0, 8, end - i))
        y[i:end] += env * np.sin(2 * np.pi * 300 * np.linspace(0, (end - i) / sr, end - i))
        t += period
    return y


@pytest.fixture
def fake_original(monkeypatch: pytest.MonkeyPatch):
    """Stand in for librosa.load(original_path) with a synthetic signal, so
    these tests don't need a real audio file on disk."""

    def _install(y: np.ndarray, sr: int = SR) -> None:
        import librosa

        monkeypatch.setattr(librosa, "load", lambda path, mono=True: (y, sr))

    return _install


def test_detect_bpm_recovers_synthetic_click_train(fake_original):
    """Deterministic signal + deterministic algorithm -> a fixed tolerance
    is safe, not flaky. 120 BPM click train, no windows to trim."""
    fake_original(_click_train(bpm=120.0, duration=8.0))

    bpm = detect_bpm("unused.wav")

    assert bpm is not None
    assert abs(bpm - 120.0) < 15  # beat trackers are approximate, not exact


def test_detect_bpm_none_for_silent_audio(tmp_path: Path):
    p = tmp_path / "silent.wav"
    sf.write(str(p), np.zeros(SR * 2), SR)
    assert detect_bpm(p) is None


def test_trim_silence_missing_file_is_a_noop():
    y = np.arange(1000, dtype=float)
    trimmed = _trim_silence(y, sr=100, windows_path="does-not-exist.json")
    assert len(trimmed) == len(y)


def test_trim_silence_excludes_window_range(tmp_path: Path):
    y = np.arange(1000, dtype=float)
    sr = 100  # 1 sample = 0.01s -> window [1.0, 3.0)s = samples [100, 300)
    windows_path = tmp_path / "windows.json"
    windows_path.write_text(json.dumps([{"start": 1.0, "end": 3.0}]))

    trimmed = _trim_silence(y, sr, windows_path)

    assert len(trimmed) == len(y) - 200
    assert not set(range(100, 300)) & set(trimmed.tolist())


def test_detect_bpm_windows_path_excludes_count_in(fake_original, tmp_path: Path):
    """A fast click train stuffed into the first two seconds (standing in for
    count-in clicks) must not drag the detected tempo toward its own rate once
    that region is excluded via windows_path."""
    music = _click_train(bpm=100.0, duration=10.0)
    count_in = _click_train(bpm=300.0, duration=2.0)
    fake_original(np.concatenate([count_in, music]))

    windows_path = tmp_path / "windows.json"
    windows_path.write_text(json.dumps([{"start": 0.0, "end": 2.0}]))

    bpm = detect_bpm("unused.wav", windows_path=windows_path)

    assert bpm is not None
    assert abs(bpm - 100.0) < 15
