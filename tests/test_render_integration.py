# tests/test_render_integration.py
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from bassify.paths import resolve_paths
from bassify.pipeline import run_pipeline
from bassify.render import render_track
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


@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
def test_render_errors_without_bass_wav(tmp_path: Path) -> None:
    lonely = tmp_path / "99_Nope_bass_only.m4a"
    lonely.write_bytes(b"not really an m4a")
    with pytest.raises(FileNotFoundError):
        render_track(lonely, "still")
