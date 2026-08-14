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
    """When art extraction succeeds, covr is set on the MP4 object."""
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

    # Capture what gets set on the MP4 instance (patch at mutagen source so the
    # local `from mutagen.mp4 import MP4` inside encode_track picks it up).
    from unittest.mock import MagicMock, patch

    fake_mp4 = MagicMock()
    fake_mp4.tags = None

    out = tmp_path / "track.m4a"
    with patch("mutagen.mp4.MP4", return_value=fake_mp4):
        from bassify.encode import encode_track

        encode_track(wav, orig, output=out, force=False)

    # covr was set on the MP4 instance
    set_calls = {call[0][0]: call[0][1] for call in fake_mp4.__setitem__.call_args_list}
    assert "covr" in set_calls, "covr was not written to the MP4 object"


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
            "color=red:size=16x16:duration=0.1",
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


# ---------------------------------------------------------------------------
# title_suffix unit tests (no real ffmpeg)
# ---------------------------------------------------------------------------


def test_encode_track_title_suffix_appended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: E501
    """When title_suffix is given, it is appended to the existing title tag."""
    wav = tmp_path / "track.wav"
    wav.write_bytes(b"RIFF")
    orig = tmp_path / "track.mp3"
    orig.write_bytes(b"ID3")

    import bassify.encode as enc_mod

    def fake_run_ffmpeg(args: list) -> None:
        out_path = Path(args[-1])
        out_path.write_bytes(b"ftyp_m4a_stub")

    monkeypatch.setattr(enc_mod, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(enc_mod, "_extract_cover_to_file", lambda orig, dest: False)

    # Patch mutagen.mp4.MP4 so the local `from mutagen.mp4 import MP4` inside
    # encode_track picks up our fake. Pre-load it with a title tag.
    from unittest.mock import MagicMock, patch

    fake_tags = {"\xa9nam": ["Turnarounds"]}
    fake_mp4 = MagicMock()
    fake_mp4.tags = fake_tags

    # Track what gets __setitem__-ed on the instance (bound as a method, receives self too)
    set_calls: dict = {}

    def record_setitem(_self, k, v):
        set_calls[k] = v
        fake_tags[k] = v

    fake_mp4.__setitem__ = record_setitem

    out = tmp_path / "track.m4a"
    with patch("mutagen.mp4.MP4", return_value=fake_mp4):
        from bassify.encode import encode_track

        encode_track(wav, orig, output=out, force=False, title_suffix="(Bass Only)")

    assert "\xa9nam" in set_calls, "title key was never set"
    assert set_calls["\xa9nam"] == ["Turnarounds (Bass Only)"]


def test_encode_track_title_suffix_none_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: E501
    """When title_suffix is None (default), MP4 is not opened (no art, no suffix)."""
    wav = tmp_path / "track.wav"
    wav.write_bytes(b"RIFF")
    orig = tmp_path / "track.mp3"
    orig.write_bytes(b"ID3")

    import bassify.encode as enc_mod

    def fake_run_ffmpeg(args: list) -> None:
        out_path = Path(args[-1])
        out_path.write_bytes(b"ftyp_m4a_stub")

    monkeypatch.setattr(enc_mod, "run_ffmpeg", fake_run_ffmpeg)
    # Art extraction fails -> no covr branch entered
    monkeypatch.setattr(enc_mod, "_extract_cover_to_file", lambda orig, dest: False)

    mp4_calls = []

    from unittest.mock import patch

    with patch("mutagen.mp4.MP4", side_effect=lambda p: mp4_calls.append(p) or object()):
        from bassify.encode import encode_track

        out = tmp_path / "track.m4a"
        encode_track(wav, orig, output=out, force=False, title_suffix=None)

    # With no art and no title_suffix, MP4 should never be opened
    assert mp4_calls == [], f"MP4 was unexpectedly opened: {mp4_calls}"


# ---------------------------------------------------------------------------
# ffmpeg-gated integration test: title_suffix lands in real m4a
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
def test_encode_track_title_suffix_real(tmp_path: Path) -> None:
    """Encode with title_suffix; verify title tag ends with the suffix in real m4a."""
    # Build tiny WAV
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

    # Build tiny cover image
    tiny_jpg = tmp_path / "cover.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=red:size=16x16:duration=0.1",
            "-frames:v",
            "1",
            str(tiny_jpg),
        ],
        check=True,
        capture_output=True,
    )

    # Build source mp3 with title tag + cover art
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
            "-metadata",
            "title=Turnarounds",
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

    # Encode with suffix
    out_suffix = tmp_path / "result_suffix.m4a"
    encode_track(wav, original, output=out_suffix, force=True, title_suffix="(Bass Only)")

    from mutagen.mp4 import MP4

    mp4 = MP4(str(out_suffix))
    assert mp4.tags is not None
    title_vals = mp4.tags.get("\xa9nam", [])
    assert title_vals, "title tag missing"
    assert title_vals[0].endswith("(Bass Only)"), (
        f"expected title ending '(Bass Only)', got: {title_vals[0]!r}"
    )
    assert "covr" in mp4.tags, "covr atom should still be present"

    # Encode without suffix -> title should NOT end with "(Bass Only)"
    out_no_suffix = tmp_path / "result_no_suffix.m4a"
    encode_track(wav, original, output=out_no_suffix, force=True, title_suffix=None)

    mp4_no = MP4(str(out_no_suffix))
    assert mp4_no.tags is not None
    title_no = mp4_no.tags.get("\xa9nam", [])
    if title_no:
        assert not title_no[0].endswith("(Bass Only)"), (
            f"title should not have suffix, got: {title_no[0]!r}"
        )
