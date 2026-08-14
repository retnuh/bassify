"""Tests for encode_track() in bassify.encode.

Unit tests use monkeypatching to avoid real ffmpeg calls.
The ffmpeg-gated integration test proves the real covr atom lands on disk.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
skip_reason = "ffmpeg/ffprobe not on PATH"

TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),\x01"
    b"\x02\x03\xff\xd9"
)

TINY_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8  # magic only (enough for format detection)


# ---------------------------------------------------------------------------
# Unit tests (no real ffmpeg)
# ---------------------------------------------------------------------------


def test_encode_track_skip_when_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """encode_track returns immediately (printing skip) when output exists and force=False."""
    wav = tmp_path / "track.wav"
    wav.write_bytes(b"RIFF")
    orig = tmp_path / "track.mp3"
    orig.write_bytes(b"ID3")
    out = tmp_path / "track.m4a"
    out.write_bytes(b"ftyp")  # pre-existing output

    run_calls = []

    import bassify.encode as enc_mod

    monkeypatch.setattr(enc_mod, "run_ffmpeg", lambda args: run_calls.append(args))

    from bassify.encode import encode_track

    result = encode_track(wav, orig, output=out, force=False)
    assert result == out
    assert run_calls == []  # ffmpeg not called


def test_encode_track_no_art_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When art extraction fails, encode_track still returns the m4a without raising."""
    wav = tmp_path / "track.wav"
    wav.write_bytes(b"RIFF")
    orig = tmp_path / "track.mp3"
    orig.write_bytes(b"ID3")

    import bassify.encode as enc_mod

    # ffmpeg audio encode succeeds (creates the output file as a side-effect)
    def fake_run_ffmpeg(args: list) -> None:
        out_path = Path(args[-1])
        out_path.write_bytes(b"ftyp_m4a_stub")

    monkeypatch.setattr(enc_mod, "run_ffmpeg", fake_run_ffmpeg)
    # Art extraction always fails
    monkeypatch.setattr(enc_mod, "_extract_cover_to_file", lambda orig, dest: False)

    from bassify.encode import encode_track

    out = tmp_path / "track.m4a"
    result = encode_track(wav, orig, output=out, force=False)
    assert result == out
    assert out.exists()


def test_encode_track_with_art_embeds_covr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When art extraction succeeds, _embed_covr is called with the art bytes."""
    wav = tmp_path / "track.wav"
    wav.write_bytes(b"RIFF")
    orig = tmp_path / "track.mp3"
    orig.write_bytes(b"ID3")

    import bassify.encode as enc_mod

    def fake_run_ffmpeg(args: list) -> None:
        out_path = Path(args[-1])
        out_path.write_bytes(b"ftyp_m4a_stub")

    monkeypatch.setattr(enc_mod, "run_ffmpeg", fake_run_ffmpeg)

    # Art extraction "succeeds" by writing a fake JPEG to dest
    def fake_extract_cover(original: Path, dest: Path) -> bool:
        dest.write_bytes(TINY_JPEG)
        return True

    monkeypatch.setattr(enc_mod, "_extract_cover_to_file", fake_extract_cover)

    embedded = []

    def fake_embed_covr(out: Path, art_data: bytes) -> None:
        embedded.append((out, art_data))

    monkeypatch.setattr(enc_mod, "_embed_covr", fake_embed_covr)

    from bassify.encode import encode_track

    out = tmp_path / "track.m4a"
    encode_track(wav, orig, output=out, force=False)

    assert len(embedded) == 1
    assert embedded[0][0] == out
    assert embedded[0][1] == TINY_JPEG


def test_detect_image_format_png() -> None:
    """PNG magic bytes are recognized as FORMAT_PNG."""
    from mutagen.mp4 import MP4Cover

    from bassify.encode import _detect_image_format

    assert _detect_image_format(TINY_PNG) == MP4Cover.FORMAT_PNG


def test_detect_image_format_jpeg() -> None:
    """JPEG (non-PNG) bytes default to FORMAT_JPEG."""
    from mutagen.mp4 import MP4Cover

    from bassify.encode import _detect_image_format

    assert _detect_image_format(TINY_JPEG) == MP4Cover.FORMAT_JPEG


# ---------------------------------------------------------------------------
# ffmpeg-gated integration test: prove real covr atom lands
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
def test_encode_track_real_covr_atom(tmp_path: Path) -> None:
    """Encode a synthetic m4a from a source with embedded cover art; verify covr tag present."""
    # Build a tiny stereo WAV source (1s sine wave, no silence needed)
    wav = tmp_path / "bass.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:a",
            "pcm_s16le",
            str(wav),
        ],
        check=True,
        capture_output=True,
    )

    # Build a tiny synthetic JPEG (1x1 red pixel) as cover art image
    tiny_jpg = tmp_path / "cover.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=red:size=1x1:duration=0.1",
            "-frames:v",
            "1",
            str(tiny_jpg),
        ],
        check=True,
        capture_output=True,
    )

    # Build a synthetic mp3 with embedded cover art (our "original" source)
    original = tmp_path / "original.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-i",
            str(tiny_jpg),
            "-map",
            "0:a",
            "-map",
            "1:v",
            "-c:a",
            "libmp3lame",
            "-c:v",
            "copy",
            "-id3v2_version",
            "3",
            "-metadata:s:v",
            "title=Album cover",
            "-metadata:s:v",
            "comment=Cover (front)",
            str(original),
        ],
        check=True,
        capture_output=True,
    )

    from bassify.encode import encode_track

    out = tmp_path / "result.m4a"
    encode_track(wav, original, output=out, force=True)

    assert out.exists(), "output m4a was not created"

    from mutagen.mp4 import MP4

    mp4 = MP4(str(out))
    assert mp4.tags is not None, "m4a has no tags at all"
    assert "covr" in mp4.tags, "covr atom missing from m4a tags"
    assert len(mp4.tags["covr"]) > 0, "covr atom is empty"
