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
def test_extract_bass_produces_frame_exact_dirty_and_clean(tmp_path: Path) -> None:
    """bass_clean.wav is mono and frame-count-exact with bass.wav."""
    src = tmp_path / "Coll" / "track.wav"
    _make_source(src)

    dirty_out = tmp_path / "bass.wav"
    clean_out = tmp_path / "bass_clean.wav"
    extract_bass(src, output=dirty_out, output_clean=clean_out, cut_inputs=True)

    assert dirty_out.exists()
    assert clean_out.exists()
    with wave.open(str(clean_out), "rb") as w:
        assert w.getnchannels() == 1, f"expected mono, got {w.getnchannels()} channels"
    with wave.open(str(dirty_out), "rb") as wd, wave.open(str(clean_out), "rb") as wc:
        assert wc.getnframes() == wd.getnframes(), (
            f"frame count mismatch: dirty={wd.getnframes()} clean={wc.getnframes()}"
        )


@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
def test_bass_clean_backstop_attenuates_above_the_cutoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The written bass_clean.wav must actually be lowpassed.

    No test asserted this before, which is why the asupercut backstop could
    fail on every single track without anything going red. A 2000 Hz tone
    sits well past the 800 Hz corner; at 12 poles it should be crushed
    relative to a 200 Hz tone of equal input amplitude.

    The 40 dB bound is deliberately loose -- the 4-pole fallback would pass
    it too. Distinguishing 4 poles from 12 is the job of the
    BACKSTOP_FILTER constant test; this test's job is proving the filter
    runs at all.
    """
    import numpy as np
    import soundfile as sf
    from scipy.signal import butter, sosfiltfilt

    # NOTE: two deviations from the brief's literal fixture, both verified
    # necessary by actually running Step 4 (temporarily gutting the backstop
    # and re-running):
    #
    # 1. Duration: sr*3 (3s) -> sr*6 (6s). bass_free_frame_mask selects a
    #    fixed 30th percentile of frames as "bass-free" regardless of clip
    #    length, while fit_projection_gains requires >=1.0s worth of such
    #    frames (a fixed frame count, independent of clip duration). 30% of
    #    3s (~0.9s) falls just short of that fixed 1.0s floor, so a 3s clip
    #    always raised InsufficientCalibrationData before the backstop ever
    #    ran. 6s clears the floor with margin (~1.8s of bass-free frames).
    #
    # 2. Reference tone: R no longer carries a bit-for-bit-identical copy of
    #    the 2kHz leak. With an exact copy, the linear per-bin projection
    #    alone (project_clean_bass) cancels the 2kHz tone to ~-77dB with NO
    #    backstop at all -- confirmed by stubbing the backstop call and
    #    re-running, per Step 4. That means the original fixture couldn't
    #    prove the backstop does anything; it would pass even with the
    #    filter deleted, recreating exactly the blind spot this test exists
    #    to close. Adding small independent (uncorrelated) per-channel noise
    #    models the fact that real guitar bleed is never a perfectly
    #    coherent copy between channels -- the projection can't cancel the
    #    part of a channel's content that has no counterpart in the other
    #    channel, so a real residual is left behind for the lowpass backstop
    #    to actually remove. Verified: stubbed backstop -> -30.3dB (fails
    #    the < -40dB bound, as required); real backstop -> -123.5dB (passes
    #    with wide margin).
    sr = 44100
    t = np.arange(sr * 6) / sr
    rng = np.random.default_rng(0)
    low_tone = 0.3 * np.sin(2 * np.pi * 200 * t)
    high_tone = 0.3 * np.sin(2 * np.pi * 2000 * t)
    noise_l = 0.04 * rng.standard_normal(len(t))
    noise_r = 0.04 * rng.standard_normal(len(t))
    # L carries both; R carries only the high tone (plus its own independent
    # noise), so the projection has a reference to cancel and the low tone
    # survives as "bass".
    stereo = np.stack([low_tone + high_tone + noise_l, high_tone + noise_r], axis=1)

    src = tmp_path / "tones.wav"
    sf.write(str(src), stereo, sr, subtype="PCM_24")

    monkeypatch.chdir(tmp_path)
    extract_bass(src, force=True)
    clean_path = resolve_paths(src).bass_clean

    y, out_sr = sf.read(str(clean_path), dtype="float64", always_2d=False)

    def band_rms(low, high):
        sos = butter(4, [low, high], btype="bandpass", fs=out_sr, output="sos")
        return float(np.sqrt(np.mean(sosfiltfilt(sos, y) ** 2)))

    low_rms = band_rms(150, 250)
    high_rms = band_rms(1800, 2200)
    ratio_db = 20 * np.log10(max(high_rms, 1e-12) / max(low_rms, 1e-12))

    assert ratio_db < -40, f"backstop did not attenuate: 2kHz is only {ratio_db:.1f}dB below 200Hz"


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


# ---------------------------------------------------------------------------
# Real-track regression: known-bad tracks 06 and 40
# ---------------------------------------------------------------------------
#
# Scored with metrics.compute_music_band_delta_db / compute_absolute_leak_db
# (dirty-vs-clean band comparison during active music, plus an absolute
# cleanliness score against the original track) -- NOT the earlier
# silence-window residual approach, which was found during manual review to
# conflate voice/narration bleed in count-in windows with actual guitar
# bleed during playing, producing misleading "regression" signals on
# instructional tracks with spoken narration between examples. See the
# guitar-cancellation handoff doc for the full investigation.

_REGRESSION_TRACKS_DIR = Path("tracks/BluesBass")
_REGRESSION_TRACKS = ["06_Dyna Flow.mp3", "40_The Thrill Is Gone.mp3"]
_missing_regression_tracks = [
    t for t in _REGRESSION_TRACKS if not (_REGRESSION_TRACKS_DIR / t).exists()
]


@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
@pytest.mark.skipif(
    bool(_missing_regression_tracks),
    reason=f"real tracks not found locally: {_missing_regression_tracks}",
)
@pytest.mark.parametrize("track_name", _REGRESSION_TRACKS)
def test_clean_bass_reduces_leak_on_known_bad_tracks(
    track_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression check on real known-bad tracks: the clean bass must show
    genuine, substantial leak reduction relative to both the naive baseline
    and the original track -- not just a directionally-better number."""
    from bassify.detect import detect_windows
    from bassify.metrics import compute_absolute_leak_db, compute_music_band_delta_db

    src = (_REGRESSION_TRACKS_DIR / track_name).resolve()
    monkeypatch.chdir(tmp_path)

    p = resolve_paths(src)
    extract_bass(src, force=True)
    windows = detect_windows(p.bass, original_path=src, force=True)

    high_delta, low_delta = compute_music_band_delta_db(p.bass, p.bass_clean, windows)
    absolute_leak = compute_absolute_leak_db(p.bass_clean, src, windows)

    print(
        f"{track_name}: high-band Δ={high_delta:.1f}dB low-band Δ={low_delta:.1f}dB "
        f"abs leak vs original={absolute_leak:.1f}dB"
    )

    assert high_delta < 0, f"{track_name}: high-band leak did not improve (Δ={high_delta:.1f}dB)"
    assert abs(low_delta) < 5.0, (
        f"{track_name}: bass itself moved too much (low-band Δ={low_delta:.1f}dB) "
        "-- projection may be damaging bass, not just cancelling guitar"
    )
    assert absolute_leak < -15.0, (
        f"{track_name}: not enough of the original's high-band content was removed "
        f"(abs leak={absolute_leak:.1f}dB, need < -15.0dB)"
    )
