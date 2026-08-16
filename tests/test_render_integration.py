# tests/test_render_integration.py
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from bassify.paths import resolve_paths
from bassify.pipeline import run_pipeline
from bassify.render import render_track, resolve_render_inputs
from bassify.render.key import detect_key
from bassify.slice import SliceSpec

pytestmark = pytest.mark.integration
ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
skip_reason = "ffmpeg/ffprobe not on PATH"


def _probe(path: Path, entries: str) -> str:
    return subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            entries,
            "-of",
            "default=noprint_wrappers=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _make_tagged_source(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=80:duration=1",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=d=1:c=pink:a=0.05",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo:d=1.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=80:duration=1",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=d=1:c=pink:a=0.05",
            "-filter_complex",
            "[0:a][1:a]amix=inputs=2:normalize=0[aL];[1:a]acopy[aR];"
            "[aL][aR]join=inputs=2:channel_layout=stereo[segA];"
            "[2:a]acopy[segB];"
            "[3:a][4:a]amix=inputs=2:normalize=0[cL];[4:a]acopy[cR];"
            "[cL][cR]join=inputs=2:channel_layout=stereo[segC];"
            "[segA][segB][segC]concat=n=3:v=0:a=1[out]",
            "-map",
            "[out]",
            "-metadata",
            "title=Test Track (Bass Only)",
            "-metadata",
            "artist=Tester",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
def test_render_still_and_final(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "Coll" / "01_Test.wav"
    _make_tagged_source(src)

    spec = SliceSpec(duration=3)
    run_pipeline(src, slice_spec=spec, force=True)
    bass_only = resolve_paths(src, slice_spec=spec).bass_only_m4a
    assert bass_only.exists()

    still = render_track(bass_only, "still", force=True)
    assert still.exists() and "audio" in _probe(still, "stream=codec_type")

    out = render_track(bass_only, "final", force=True)
    assert out.exists()
    ct = _probe(out, "stream=codec_type")
    assert "video" in ct and "audio" in ct
    assert "yuv420p" in _probe(out, "stream=pix_fmt")

    from PIL import Image

    thumb = out.with_name(out.name.replace("_render", "_thumbnail").replace(".mp4", ".png"))
    assert thumb.exists() and Image.open(thumb).size == (1280, 720)

    vdur = float(_probe(out, "format=duration").split("=")[1])
    adur = float(_probe(bass_only, "format=duration").split("=")[1])
    assert abs(vdur - adur) < 0.5


@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
def test_render_track_finds_original_and_detects_bpm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the source lives at tracks/<collection>/<stem>.* (the CLI's own
    layout -- see `bassify run tracks/X`), render_track must locate it and
    pass it to detect_bpm along with the real windows.json, and the result
    must reach the thumbnail without error.

    Spies on detect_bpm rather than asserting a real tempo: the input here is
    sine+noise, not music, so a musically meaningful BPM isn't the point --
    tempo.py's own unit tests already cover detection accuracy on a real
    rhythmic signal. This test is about the wiring: does render_track find
    the original and hand it the right arguments.
    """
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "tracks" / "Coll" / "01_Test.wav"
    _make_tagged_source(src)

    spec = SliceSpec(duration=3)
    run_pipeline(src, slice_spec=spec, force=True)
    bass_only = resolve_paths(src, slice_spec=spec).bass_only_m4a
    windows_json = resolve_paths(src, slice_spec=spec).windows
    assert bass_only.exists() and windows_json.exists()

    calls = []

    def fake_detect_bpm(original_path, windows_path=None):
        calls.append((Path(original_path), Path(windows_path) if windows_path else None))
        return 91.0

    monkeypatch.setattr("bassify.render.detect_bpm", fake_detect_bpm)

    out = render_track(bass_only, "final", force=True)
    assert out.exists()

    assert len(calls) == 1
    original_path, windows_path = calls[0]
    # original_path comes back from a glob() on a relative "tracks/..." base
    # (relative to cwd, chdir'd to tmp_path above), so resolve both sides
    # before comparing -- the assertion is about identity, not string form.
    assert original_path.resolve() == src.resolve()
    assert windows_path.resolve() == windows_json.resolve()

    from PIL import Image

    thumb = out.with_name(out.name.replace("_render", "_thumbnail").replace(".mp4", ".png"))
    assert thumb.exists() and Image.open(thumb).size == (1280, 720)


@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
def test_detect_key_returns_pitch_class(tmp_path: Path) -> None:
    src = tmp_path / "e2.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=82.41:duration=4",
            "-c:a",
            "pcm_s16le",
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    pc = detect_key(src)
    assert pc is None or 0 <= pc <= 11


def test_resolve_render_inputs_looks_for_bass_clean(tmp_path: Path) -> None:
    bass_only = tmp_path / "01_Song_bass_only.m4a"
    bass_only.touch()
    bass_clean = tmp_path / "01_Song_bass_clean.wav"
    bass_clean.touch()

    assert resolve_render_inputs(bass_only) == bass_clean


def test_resolve_render_inputs_raises_without_bass_clean(tmp_path: Path) -> None:
    bass_only = tmp_path / "01_Song_bass_only.m4a"
    bass_only.touch()
    # no _bass_clean.wav sibling created

    with pytest.raises(FileNotFoundError):
        resolve_render_inputs(bass_only)


@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
def test_render_errors_without_bass_clean(tmp_path: Path) -> None:
    lonely = tmp_path / "99_Nope_bass_only.m4a"
    lonely.write_bytes(b"not really an m4a")
    with pytest.raises(FileNotFoundError):
        render_track(lonely, "still")
