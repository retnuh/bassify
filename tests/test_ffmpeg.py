from pathlib import Path

import pytest

from bassify.ffmpeg import FfmpegError, parse_duration, should_skip


def test_parse_duration():
    assert parse_duration("56.633500\n") == pytest.approx(56.6335)


def test_parse_duration_bad_raises():
    with pytest.raises(FfmpegError):
        parse_duration("N/A")


def test_should_skip(tmp_path: Path):
    out = tmp_path / "x.wav"
    assert should_skip(out, force=False) is False  # does not exist
    out.write_bytes(b"x")
    assert should_skip(out, force=False) is True
    assert should_skip(out, force=True) is False
