from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
NOTE_INDEX: dict[str, int] = {n: i for i, n in enumerate(_SHARP)}
NOTE_INDEX.update({"Db": 1, "Eb": 3, "Gb": 6, "Ab": 8, "Bb": 10})

# Krumhansl-Schmuckler profiles (used by detect_key).
_MAJ = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MIN = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def root_pc(key_str: str | None) -> int | None:
    """Parse a key string ('E', 'Bm', 'F#m', 'Db') to a root pitch-class 0-11.

    The major/minor suffix is ignored — only the root matters for label tiers.
    Returns None for None/empty/unparseable input.
    """
    if not key_str:
        return None
    s = key_str.strip()
    if s.endswith("m") and not s.endswith("#m") and len(s) > 1 and s[:-1] in NOTE_INDEX:
        s = s[:-1]
    elif s.endswith("m") and len(s) > 2 and s[:-1] in NOTE_INDEX:
        s = s[:-1]
    return NOTE_INDEX.get(s)


def detect_key(bass_wav: Path | str) -> int | None:
    """Detect the root pitch-class of a track via librosa chroma + Krumhansl."""
    import librosa
    import numpy as np

    y, sr = librosa.load(str(bass_wav), mono=True)
    if y.size == 0:
        return None
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)

    def best(profile: list[float]) -> tuple[float, int]:
        p = np.array(profile)
        return max((float(np.corrcoef(np.roll(p, i), chroma)[0, 1]), i) for i in range(12))

    rmaj, imaj = best(_MAJ)
    rmin, imin = best(_MIN)
    return imaj if rmaj >= rmin else imin


def resolve_key(
    cli_key: str | None,
    override: dict,
    bass_wav: Path | str,
    _detect: Callable[[Path | str], int | None] = detect_key,
) -> int | None:
    """Resolve the effective root pitch-class by precedence:
    --key flag > sidecar override (authoritative, incl. explicit null) > auto-detect.
    """
    if cli_key:
        return root_pc(cli_key)
    if "key" in override:  # sidecar entry present (value may be None → neutral)
        return root_pc(override["key"])
    return _detect(bass_wav)
