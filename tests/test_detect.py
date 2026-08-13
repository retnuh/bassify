import pytest

from bassify.detect import parse_silences

SAMPLE = """
[silencedetect @ 0x1] silence_start: 12.284
[silencedetect @ 0x1] silence_end: 15.913 | silence_duration: 3.629
[silencedetect @ 0x1] silence_start: 47.100
[silencedetect @ 0x1] silence_end: 51.300 | silence_duration: 4.200
"""

TRAILING = """
[silencedetect @ 0x1] silence_start: 40.000
"""


def test_paired_windows_padded():
    w = parse_silences(SAMPLE, duration=60.0, pad=0.1)
    assert w[0]["start"] == pytest.approx(12.184)
    assert w[0]["end"] == pytest.approx(16.013)
    assert len(w) == 2


def test_unpaired_trailing_start_closes_at_duration():
    w = parse_silences(TRAILING, duration=45.0, pad=0.1)
    assert len(w) == 1
    assert w[0]["start"] == pytest.approx(39.9)
    assert w[0]["end"] == pytest.approx(45.0)  # clamped to duration, not duration+pad


def test_clamp_lower_bound():
    w = parse_silences("[x] silence_start: 0.05\n[x] silence_end: 1.0\n", duration=10.0, pad=0.1)
    assert w[0]["start"] == 0.0
