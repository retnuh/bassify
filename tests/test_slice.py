import pytest

from bassify.slice import SliceSpec, input_needs_cut, resolve_effective_slice


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


# ---------------------------------------------------------------------------
# SliceSpec.from_filename round-trip tests
# ---------------------------------------------------------------------------


def test_from_filename_duration_only():
    s = SliceSpec(duration=30.0)
    name = "track_bass" + s.suffix() + ".wav"
    assert SliceSpec.from_filename(name) == s


def test_from_filename_start_only():
    s = SliceSpec(start=15.0)
    name = "track_bass" + s.suffix() + ".wav"
    assert SliceSpec.from_filename(name) == s


def test_from_filename_both():
    s = SliceSpec(duration=30.0, start=15.0)
    name = "track_bass" + s.suffix() + ".wav"
    assert SliceSpec.from_filename(name) == s


def test_from_filename_float_duration():
    s = SliceSpec(duration=2.5)
    name = "track_bass" + s.suffix() + ".wav"
    assert SliceSpec.from_filename(name) == s


def test_from_filename_float_both():
    s = SliceSpec(duration=2.5, start=15.0)
    name = "track_bass" + s.suffix() + ".wav"
    assert SliceSpec.from_filename(name) == s


def test_from_filename_no_tokens_returns_empty():
    assert SliceSpec.from_filename("03_Turnarounds_bass.wav").is_empty()


def test_from_filename_no_false_positive_on_digits_in_name():
    """03_Turnarounds should NOT parse as a slice token."""
    assert SliceSpec.from_filename("03_Turnarounds_bass.wav").is_empty()


def test_from_filename_digits_in_name_with_real_tokens():
    """03_Turnarounds_bass_only_d30s_s15s.wav should parse dur=30, start=15."""
    s = SliceSpec.from_filename("03_Turnarounds_bass_only_d30s_s15s.wav")
    assert s == SliceSpec(duration=30.0, start=15.0)


def test_from_filename_suffix_round_trip_d30s():
    assert SliceSpec.from_filename("x_bass_d30s.wav") == SliceSpec(duration=30.0)


def test_from_filename_suffix_round_trip_d2_5s():
    assert SliceSpec.from_filename("x_bass_d2.5s.wav") == SliceSpec(duration=2.5)


def test_from_filename_accepts_path_object():
    from pathlib import Path
    assert SliceSpec.from_filename(Path("track_d30s.wav")) == SliceSpec(duration=30.0)


# ---------------------------------------------------------------------------
# load_kwargs tests
# ---------------------------------------------------------------------------


def test_load_kwargs_empty():
    kw = SliceSpec().load_kwargs()
    assert kw == {"offset": 0.0}
    assert "duration" not in kw


def test_load_kwargs_duration_only():
    kw = SliceSpec(duration=30.0).load_kwargs()
    assert kw == {"offset": 0.0, "duration": 30.0}


def test_load_kwargs_start_only():
    kw = SliceSpec(start=15.0).load_kwargs()
    assert kw == {"offset": 15.0}
    assert "duration" not in kw


def test_load_kwargs_both():
    kw = SliceSpec(duration=30.0, start=15.0).load_kwargs()
    assert kw == {"offset": 15.0, "duration": 30.0}


# ---------------------------------------------------------------------------
# resolve_effective_slice tests
# ---------------------------------------------------------------------------


def test_resolve_effective_slice_empty_paths_no_explicit():
    eff = resolve_effective_slice([])
    assert eff.is_empty()


def test_resolve_effective_slice_explicit_only():
    spec = SliceSpec(duration=30.0)
    eff = resolve_effective_slice([], explicit=spec)
    assert eff == spec


def test_resolve_effective_slice_from_filename():
    eff = resolve_effective_slice(["track_bass_d30s.wav"])
    assert eff == SliceSpec(duration=30.0)


def test_resolve_effective_slice_agreed_explicit_and_filename():
    spec = SliceSpec(duration=30.0)
    eff = resolve_effective_slice(["track_bass_d30s.wav"], explicit=spec)
    assert eff == spec


def test_resolve_effective_slice_multiple_files_same():
    eff = resolve_effective_slice(["track_bass_d30s.wav", "track_bass_only_d30s.wav"])
    assert eff == SliceSpec(duration=30.0)


def test_resolve_effective_slice_conflict_raises():
    with pytest.raises(ValueError, match="conflicting"):
        resolve_effective_slice(
            ["track_bass_d30s.wav"],
            explicit=SliceSpec(duration=20.0),
        )


def test_resolve_effective_slice_conflict_two_files_raises():
    with pytest.raises(ValueError, match="conflicting"):
        resolve_effective_slice(["track_bass_d30s.wav", "track_bass_d20s.wav"])


def test_resolve_effective_slice_unsuffixed_and_explicit():
    spec = SliceSpec(duration=30.0)
    # Unsuffixed file contributes empty -> ignored; explicit wins.
    eff = resolve_effective_slice(["track_bass.wav"], explicit=spec)
    assert eff == spec


def test_resolve_effective_slice_empty_explicit_ignored():
    eff = resolve_effective_slice(["track_bass_d30s.wav"], explicit=SliceSpec())
    assert eff == SliceSpec(duration=30.0)


# ---------------------------------------------------------------------------
# input_needs_cut truth table
# ---------------------------------------------------------------------------


def test_input_needs_cut_presigned_skip():
    """Pre-sliced file matching the effective spec should NOT need cutting."""
    eff = SliceSpec(duration=30.0)
    assert input_needs_cut("track_bass_d30s.wav", eff) is False


def test_input_needs_cut_unsuffixed_apply():
    """Unsuffixed file should need cutting when effective is non-empty."""
    eff = SliceSpec(duration=30.0)
    assert input_needs_cut("track.mp3", eff) is True


def test_input_needs_cut_empty_effective_no_cut():
    """When effective is empty, nothing needs cutting (no-slice run)."""
    eff = SliceSpec()
    assert input_needs_cut("track.mp3", eff) is False


def test_input_needs_cut_empty_effective_unsuffixed_no_cut():
    """Both empty: from_filename is empty and effective is empty -> no cut."""
    eff = SliceSpec()
    assert input_needs_cut("track_bass.wav", eff) is False


def test_input_needs_cut_wrong_suffix_needs_cut():
    """Pre-sliced file with a DIFFERENT slice than effective must be cut."""
    eff = SliceSpec(duration=30.0)
    assert input_needs_cut("track_bass_d20s.wav", eff) is True
