from __future__ import annotations

import json
from pathlib import Path


def _trim_silence(y, sr: int, windows_path: Path | str):
    """Drop samples inside detected silence windows (count-in clicks, spoken
    narration). Beat tracking on the raw window otherwise locks onto the
    click train's own tempo, or has speech corrupt the onset envelope -- the
    same trap that has misled other per-track metrics in this project.
    """
    import numpy as np

    path = Path(windows_path)
    if not path.exists():
        return y
    windows = json.loads(path.read_text())
    mask = np.ones(len(y), dtype=bool)
    for w in windows:
        start = int(w["start"] * sr)
        end = min(int(w["end"] * sr), len(y))
        mask[start:end] = False
    return y[mask]


def detect_bpm(original_path: Path | str, windows_path: Path | str | None = None) -> float | None:
    """Detect tempo (BPM) via librosa beat tracking on the ORIGINAL track.

    Deliberately not the isolated bass: bass-only audio starves onset
    detection of the percussive transients (drums, etc.) it needs to find a
    beat grid at all -- beat_track on a bass-only track returns only a
    handful of spurious beats across a whole song. windows_path, if given,
    excludes count-in/narration so those don't corrupt the estimate.

    Returns None if the file can't be decoded or has no audio.
    """
    import librosa
    import numpy as np

    y, sr = librosa.load(str(original_path), mono=True)
    if y.size == 0:
        return None
    if windows_path is not None:
        y = _trim_silence(y, sr, windows_path)
    if y.size == 0:
        return None
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    bpm = float(np.asarray(tempo).flat[0])
    return bpm if bpm > 0 else None
