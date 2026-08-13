from bassify.slice import SliceSpec


def test_empty_suffix_and_args():
    s = SliceSpec()
    assert s.suffix() == ""
    assert s.input_args() == []
    assert s.is_empty()


def test_duration_only():
    s = SliceSpec(duration=15)
    assert s.suffix() == "_d15s"
    assert s.input_args() == ["-t", "15"]


def test_start_only():
    s = SliceSpec(start=30)
    assert s.suffix() == "_s30s"
    assert s.input_args() == ["-ss", "30"]


def test_both_start_before_duration():
    s = SliceSpec(duration=15, start=30)
    assert s.suffix() == "_d15s_s30s"
    assert s.input_args() == ["-ss", "30", "-t", "15"]


def test_integer_float_renders_without_point_zero():
    assert SliceSpec(duration=15.0).suffix() == "_d15s"


def test_non_integer_renders_compact():
    assert SliceSpec(duration=2.5).suffix() == "_d2.5s"
