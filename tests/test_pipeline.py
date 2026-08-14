from __future__ import annotations

from bassify.extract import DEFAULT_LOWPASS
from bassify.paths import resolve_paths


def test_run_pipeline_call_order_and_cut_inputs(monkeypatch, tmp_path):
    """run_pipeline calls all 5 stages in order with correct cut_inputs values."""
    calls = []

    input_mp3 = tmp_path / "tracks" / "Band" / "01_Song.mp3"
    input_mp3.parent.mkdir(parents=True)
    input_mp3.touch()

    paths = resolve_paths(input_mp3)

    def fake_extract_bass(
        input_path,
        output=None,
        lowpass=DEFAULT_LOWPASS,
        slice_spec=None,
        cut_inputs=True,
        force=False,
    ):  # noqa: E501
        calls.append(("extract_bass", {"cut_inputs": cut_inputs, "lowpass": lowpass}))
        return paths.bass

    def fake_detect_windows(
        bass_path,
        output=None,
        threshold=-40.0,
        min_gap=1.0,
        pad_start=0.0,
        pad_end=-0.05,
        min_riff=0.5,
        slice_spec=None,
        cut_inputs=True,
        force=False,
        original_path=None,
        drop_trailing=True,
    ):  # noqa: E501
        calls.append(("detect_windows", {"cut_inputs": cut_inputs, "original_path": original_path}))
        return paths.windows

    def fake_combine_track(
        bass_path,
        original_path,
        windows_path,
        output=None,
        slice_spec=None,
        cut_inputs=True,
        force=False,
    ):  # noqa: E501
        calls.append(("combine_track", {"cut_inputs": cut_inputs}))
        return paths.combined

    def fake_remix_track(
        combined_path, original_path, output=None, slice_spec=None, cut_inputs=True, force=False
    ):  # noqa: E501
        calls.append(("remix_track", {"cut_inputs": cut_inputs}))
        return paths.remix

    encode_calls = []

    def fake_encode_track(wav_path, original_path, output=None, force=False):
        encode_calls.append(output)

    import bassify.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "extract_bass", fake_extract_bass)
    monkeypatch.setattr(pipeline_mod, "detect_windows", fake_detect_windows)
    monkeypatch.setattr(pipeline_mod, "combine_track", fake_combine_track)
    monkeypatch.setattr(pipeline_mod, "remix_track", fake_remix_track)
    monkeypatch.setattr(pipeline_mod, "encode_track", fake_encode_track)

    from bassify.pipeline import run_pipeline

    run_pipeline(input_mp3)

    # (a) call order
    assert [c[0] for c in calls] == [
        "extract_bass",
        "detect_windows",
        "combine_track",
        "remix_track",
    ]

    # (b) extract got cut_inputs=True
    assert calls[0][1]["cut_inputs"] is True

    # (c) detect/combine/remix got cut_inputs=False
    assert calls[1][1]["cut_inputs"] is False
    assert calls[2][1]["cut_inputs"] is False
    assert calls[3][1]["cut_inputs"] is False

    # (d) detect got original_path == input_path
    assert calls[1][1]["original_path"] == input_mp3

    # (e) encode called twice with the two m4a targets
    assert len(encode_calls) == 2
    assert paths.combined_m4a in encode_calls
    assert paths.remix_m4a in encode_calls


def test_run_pipeline_passes_lowpass_through(monkeypatch, tmp_path):
    """run_pipeline forwards the caller's lowpass value (including None) to extract."""
    input_mp3 = tmp_path / "tracks" / "Band" / "02_Song.mp3"
    input_mp3.parent.mkdir(parents=True)
    input_mp3.touch()

    paths = resolve_paths(input_mp3)
    received = {}

    def fake_extract_bass(
        input_path,
        output=None,
        lowpass=DEFAULT_LOWPASS,
        slice_spec=None,
        cut_inputs=True,
        force=False,
    ):  # noqa: E501
        received["lowpass"] = lowpass
        return paths.bass

    def fake_detect_windows(
        bass_path,
        output=None,
        threshold=-40.0,
        min_gap=1.0,
        pad_start=0.0,
        pad_end=-0.05,
        min_riff=0.5,
        slice_spec=None,
        cut_inputs=True,
        force=False,
        original_path=None,
        drop_trailing=True,
    ):  # noqa: E501
        return paths.windows

    def fake_combine_track(
        bass_path,
        original_path,
        windows_path,
        output=None,
        slice_spec=None,
        cut_inputs=True,
        force=False,
    ):  # noqa: E501
        return paths.combined

    def fake_remix_track(
        combined_path, original_path, output=None, slice_spec=None, cut_inputs=True, force=False
    ):  # noqa: E501
        return paths.remix

    def fake_encode_track(wav_path, original_path, output=None, force=False):
        pass

    import bassify.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "extract_bass", fake_extract_bass)
    monkeypatch.setattr(pipeline_mod, "detect_windows", fake_detect_windows)
    monkeypatch.setattr(pipeline_mod, "combine_track", fake_combine_track)
    monkeypatch.setattr(pipeline_mod, "remix_track", fake_remix_track)
    monkeypatch.setattr(pipeline_mod, "encode_track", fake_encode_track)

    from bassify.pipeline import run_pipeline

    # default lowpass (DEFAULT_LOWPASS)
    run_pipeline(input_mp3)
    assert received["lowpass"] == DEFAULT_LOWPASS

    # explicit None (no-lowpass)
    run_pipeline(input_mp3, lowpass=None)
    assert received["lowpass"] is None

    # custom value
    run_pipeline(input_mp3, lowpass=500.0)
    assert received["lowpass"] == 500.0
