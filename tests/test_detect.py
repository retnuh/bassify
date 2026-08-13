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
    w = parse_silences(SAMPLE, duration=60.0, pad_start=0.1, pad_end=0.1)
    assert w[0]["start"] == pytest.approx(12.184)
    assert w[0]["end"] == pytest.approx(16.013)
    assert len(w) == 2


def test_unpaired_trailing_start_closes_at_duration():
    w = parse_silences(TRAILING, duration=45.0, pad_start=0.1, pad_end=0.1)
    assert len(w) == 1
    assert w[0]["start"] == pytest.approx(39.9)
    assert w[0]["end"] == pytest.approx(45.0)  # clamped to duration, not duration+pad


def test_clamp_lower_bound():
    w = parse_silences(
        "[x] silence_start: 0.05\n[x] silence_end: 1.0\n",
        duration=10.0,
        pad_start=0.1,
        pad_end=0.1,
    )
    assert w[0]["start"] == 0.0


# UAT real-world case: sub-millisecond transient splits one ~7.3s gap into two
# overlapping windows after ±0.1s padding. They must be merged into one.
UAT_OVERLAP = """
[silencedetect @ 0x1] silence_start: 111.301088
[silencedetect @ 0x1] silence_end: 114.721973 | silence_duration: 3.420885
[silencedetect @ 0x1] silence_start: 114.722721
[silencedetect @ 0x1] silence_end: 118.621837 | silence_duration: 3.899116
"""


def test_merge_overlapping_windows_after_padding():
    """Two windows that overlap after ±0.1s pad should merge into one."""
    w = parse_silences(UAT_OVERLAP, duration=140.0, pad_start=0.1, pad_end=0.1)
    assert len(w) == 1, f"expected 1 merged window, got {len(w)}: {w}"
    assert w[0]["start"] == pytest.approx(111.201088)
    assert w[0]["end"] == pytest.approx(118.721837)


NON_OVERLAPPING = """
[silencedetect @ 0x1] silence_start: 10.0
[silencedetect @ 0x1] silence_end: 15.0 | silence_duration: 5.0
[silencedetect @ 0x1] silence_start: 20.0
[silencedetect @ 0x1] silence_end: 25.0 | silence_duration: 5.0
"""


def test_non_overlapping_windows_stay_separate():
    """Windows with a clear gap after padding must remain as two distinct windows."""
    w = parse_silences(NON_OVERLAPPING, duration=60.0, pad_start=0.1, pad_end=0.1)
    assert len(w) == 2, f"expected 2 windows, got {len(w)}: {w}"
    assert w[0]["start"] == pytest.approx(9.9)
    assert w[0]["end"] == pytest.approx(15.1)
    assert w[1]["start"] == pytest.approx(19.9)
    assert w[1]["end"] == pytest.approx(25.1)


EXACT_SILENCE = """
[silencedetect @ 0x1] silence_start: 10.0
[silencedetect @ 0x1] silence_end: 20.0 | silence_duration: 10.0
"""


def test_pad_zero_gives_exact_detected_bounds():
    """pad_start=0 and pad_end=0 must return the raw detected silence bounds unchanged."""
    w = parse_silences(EXACT_SILENCE, duration=30.0, pad_start=0.0, pad_end=0.0)
    assert len(w) == 1
    assert w[0]["start"] == pytest.approx(10.0)
    assert w[0]["end"] == pytest.approx(20.0)


def test_pad_end_negative_pulls_only_end_earlier():
    """pad_end=-0.05 must move end earlier by 0.05s; start must be unchanged."""
    w = parse_silences(EXACT_SILENCE, duration=30.0, pad_start=0.0, pad_end=-0.05)
    assert len(w) == 1
    assert w[0]["start"] == pytest.approx(10.0)
    assert w[0]["end"] == pytest.approx(19.95)


def test_pad_start_negative_pulls_only_start_later():
    """pad_start=-0.05 must move start later by 0.05s; end must be unchanged."""
    w = parse_silences(EXACT_SILENCE, duration=30.0, pad_start=-0.05, pad_end=0.0)
    assert len(w) == 1
    assert w[0]["start"] == pytest.approx(10.05)
    assert w[0]["end"] == pytest.approx(20.0)


def test_degenerate_window_dropped_after_aggressive_pad_end():
    """A very short window where pad_end makes end <= start must be dropped entirely."""
    # Raw silence 10.0..10.03; pad_end=-0.05 → end = 10.03 - 0.05 = 9.98 < start=10.0 → drop.
    short_silence = "[x] silence_start: 10.0\n[x] silence_end: 10.03\n"
    w = parse_silences(short_silence, duration=30.0, pad_start=0.0, pad_end=-0.05)
    assert w == [], f"expected empty list (degenerate window dropped), got {w}"


def test_default_params_apply_pad_end_negative():
    """Default pad_start=0.0, pad_end=-0.05 must shift only end earlier."""
    w = parse_silences(EXACT_SILENCE, duration=30.0)
    assert len(w) == 1
    assert w[0]["start"] == pytest.approx(10.0)
    assert w[0]["end"] == pytest.approx(19.95)
