from __future__ import annotations

from pathlib import Path

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


# ---------------------------------------------------------------------------
# run_batch tests
# ---------------------------------------------------------------------------


def _make_batch_dir(tmp_path):
    """Create a tmp dir with 2 mp3s, 1 flac, 1 wav, 1 txt, 1 m4a and return dir + mp3/flac paths."""
    d = tmp_path / "tracks" / "Band"
    d.mkdir(parents=True)
    mp3a = d / "01_Alpha.mp3"
    mp3b = d / "03_Charlie.mp3"
    flac = d / "02_Bravo.flac"
    mp3a.touch()
    mp3b.touch()
    flac.touch()
    (d / "bass.wav").touch()
    (d / "combined.m4a").touch()
    (d / "notes.txt").touch()
    return d, sorted([mp3a, mp3b, flac])


def test_run_batch_calls_pipeline_per_source_file_sorted(monkeypatch, tmp_path):
    """run_batch calls run_pipeline once per .mp3/.flac in sorted order, skips .wav/.txt/.m4a."""
    batch_dir, source_tracks = _make_batch_dir(tmp_path)
    called_with = []

    def fake_run_pipeline(input_path, **kwargs):
        called_with.append(Path(input_path))

    import bassify.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "run_pipeline", fake_run_pipeline)

    from bassify.pipeline import run_batch

    run_batch(batch_dir)

    assert called_with == source_tracks
    assert len(called_with) == 3  # 2 mp3 + 1 flac; .wav/.m4a/.txt excluded


def test_run_batch_empty_dir_does_not_call_pipeline(monkeypatch, tmp_path):
    """run_batch with no source files prints a message and does NOT call run_pipeline."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    (empty_dir / "bass.wav").touch()
    (empty_dir / "notes.txt").touch()

    called = []

    def fake_run_pipeline(input_path, **kwargs):
        called.append(input_path)

    import bassify.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "run_pipeline", fake_run_pipeline)

    from bassify.pipeline import run_batch

    run_batch(empty_dir)

    assert called == []


def test_run_batch_one_failure_does_not_stop_others(monkeypatch, tmp_path):
    """A track that raises during run_pipeline is logged; remaining tracks still processed."""
    d = tmp_path / "tracks" / "Band"
    d.mkdir(parents=True)
    tracks = [d / f"0{i}_Song.mp3" for i in range(1, 4)]
    for t in tracks:
        t.touch()

    attempted = []

    def fake_run_pipeline(input_path, **kwargs):
        attempted.append(Path(input_path))
        if Path(input_path) == tracks[1]:
            raise RuntimeError("simulated ffmpeg failure")

    import bassify.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "run_pipeline", fake_run_pipeline)

    from bassify.pipeline import run_batch

    run_batch(d)  # must not raise

    assert attempted == sorted(tracks)  # all 3 attempted
    assert len(attempted) == 3
