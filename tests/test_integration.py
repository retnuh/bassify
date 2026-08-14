"""End-to-end integration tests using synthetic stereo audio.

Source design rationale
-----------------------
A pure sine+noise source with no silent gaps causes detect_windows to find ZERO
windows (silencedetect finds nothing), making the full pipeline degenerate.  To
guarantee >=1 window AND exercise the count-in-refinement code path (which runs
on each window), the synthetic source is structured as three segments:

  [0.0 – 1.0s]  sine=80Hz mixed with pink noise (simulates bass+instrument)
  [1.0 – 2.5s]  silence (> min_gap=1.0s default → silencedetect finds it)
  [2.5 – 3.5s]  sine+noise again (simulates post-count-in riff)

Total duration ≈ 3.5s.  detect_windows will find one silence window and
refine_window_full will be called for it.  Because there are no real count-in
clicks the librosa refinement falls through to its no-guitar-onset fallback
path, which is a valid code path and should not raise.
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from bassify.extract import extract_bass
from bassify.paths import resolve_paths
from bassify.pipeline import run_pipeline
from bassify.slice import SliceSpec

pytestmark = pytest.mark.integration

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
skip_reason = "ffmpeg/ffprobe not on PATH"


def _make_source(path: Path) -> None:
    """Create a synthetic stereo WAV.

    Structure (see module docstring for rationale):
      - 0–1s:   L = 80 Hz sine + pink noise, R = pink noise only
      - 1–2.5s: silence on both channels
      - 2.5–3.5s: same L/R pattern as the first segment

    The silent segment is produced via the 'anullsrc' source (true digital zero),
    keeping the pink noise seed consistent doesn't matter here.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Build each stereo segment then concat them with ffmpeg.
    # Segment A: 1s of sine+noise / noise (stereo)
    # Segment B: 1.5s of silence (stereo)
    # Segment C: 1s of sine+noise / noise (stereo)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        # ---- Segment A inputs ----
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=80:duration=1",  # idx 0: 80Hz sine 1s
        "-f",
        "lavfi",
        "-i",
        "anoisesrc=d=1:c=pink:a=0.05",  # idx 1: noise 1s
        # ---- Segment B: silence ----
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=44100:cl=stereo:d=1.5",  # idx 2: 1.5s stereo silence
        # ---- Segment C inputs ----
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=80:duration=1",  # idx 3: 80Hz sine 1s
        "-f",
        "lavfi",
        "-i",
        "anoisesrc=d=1:c=pink:a=0.05",  # idx 4: noise 1s
        "-filter_complex",
        # Segment A: L = sine+noise, R = noise
        "[0:a][1:a]amix=inputs=2:normalize=0[aL];"
        "[1:a]acopy[aR];"
        "[aL][aR]join=inputs=2:channel_layout=stereo[segA];"
        # Segment B: already stereo silence
        "[2:a]acopy[segB];"
        # Segment C: same structure as A
        "[3:a][4:a]amix=inputs=2:normalize=0[cL];"
        "[4:a]acopy[cR];"
        "[cL][cR]join=inputs=2:channel_layout=stereo[segC];"
        # Concatenate A + B + C
        "[segA][segB][segC]concat=n=3:v=0:a=1[out]",
        "-map",
        "[out]",
        "-c:a",
        "pcm_s16le",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
def test_extract_produces_mono_wav(tmp_path: Path) -> None:
    """extract_bass on synthetic stereo yields a mono WAV that exists on disk."""
    src = tmp_path / "Coll" / "track.wav"
    _make_source(src)
    out = extract_bass(src, output=tmp_path / "bass.wav", cut_inputs=True)
    assert out.exists(), f"output not found: {out}"
    with wave.open(str(out), "rb") as w:
        assert w.getnchannels() == 1, f"expected mono, got {w.getnchannels()} channels"


@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
def test_full_pipeline_slice_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """run_pipeline with slice_spec produces all six artifacts bearing the slice suffix.

    monkeypatch.chdir ensures resolve_paths writes out/ into tmp_path, not the repo.
    """
    # Hermetic: make CWD = tmp_path so out/<collection>/<track>/ lands in tmp_path.
    monkeypatch.chdir(tmp_path)

    src = tmp_path / "Coll" / "track.wav"
    _make_source(src)

    spec = SliceSpec(duration=2)
    suffix = spec.suffix()  # "_d2s" — derived from SliceSpec, not hardcoded

    run_pipeline(src, slice_spec=spec, force=True)

    p = resolve_paths(src, slice_spec=spec)
    artifacts = (p.bass, p.windows, p.bass_only, p.remix, p.bass_only_m4a, p.remix_m4a)
    for artifact in artifacts:
        assert artifact.exists(), f"missing artifact: {artifact}"
        assert suffix in artifact.name, f"expected '{suffix}' in '{artifact.name}'"


@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
def test_full_pipeline_length_invariant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No-slice run_pipeline: bass_only must equal the bass frame count exactly.

    This is the production path (no --duration/--start). The length invariant
    underpins remix channel pairing and future video sync.
    """
    monkeypatch.chdir(tmp_path)

    src = tmp_path / "Coll" / "track.wav"
    _make_source(src)

    run_pipeline(src, force=True)

    p = resolve_paths(src)
    with wave.open(str(p.bass), "rb") as wb, wave.open(str(p.bass_only), "rb") as wc:
        assert wc.getnframes() == wb.getnframes(), (
            f"length invariant broken: bass_only {wc.getnframes()} != bass {wb.getnframes()}"
        )


@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
def test_sliced_pipeline_length_invariant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sliced run_pipeline: bass_only and remix must equal the bass frame count.

    This is the regression test for the bug where bass_only/remix came out at
    full-source length (e.g. 168000 frames) while bass was correctly sliced
    (e.g. 96000 frames).  After the fix, downstream stages reconcile the slice
    from the bass filename and correctly cut the full original to match.
    """
    monkeypatch.chdir(tmp_path)

    src = tmp_path / "Coll" / "track.wav"
    _make_source(src)

    spec = SliceSpec(duration=2)
    run_pipeline(src, slice_spec=spec, force=True)

    p = resolve_paths(src, slice_spec=spec)
    with (
        wave.open(str(p.bass), "rb") as wb,
        wave.open(str(p.bass_only), "rb") as wc,
        wave.open(str(p.remix), "rb") as wr,
    ):
        bass_frames = wb.getnframes()
        bass_only_frames = wc.getnframes()
        remix_frames = wr.getnframes()
        assert bass_only_frames == bass_frames, (
            f"sliced length invariant broken: bass_only {bass_only_frames} != bass {bass_frames}"
        )
        assert remix_frames == bass_frames, (
            f"sliced length invariant broken: remix {remix_frames} != bass {bass_frames}"
        )
