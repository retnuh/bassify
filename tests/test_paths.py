from pathlib import Path

from bassify.paths import resolve_paths
from bassify.slice import SliceSpec


def test_default_layout():
    p = resolve_paths(Path("tracks/BluesBass/01_The Twelve Bar Blues Form.mp3"))
    base = Path("out/BluesBass/01_The Twelve Bar Blues Form")
    assert p.track_dir == base
    assert p.bass == base / "01_The Twelve Bar Blues Form_bass.wav"
    assert p.windows == base / "01_The Twelve Bar Blues Form_silence_windows.json"
    assert p.bass_only == base / "01_The Twelve Bar Blues Form_bass_only.wav"
    assert p.remix == base / "01_The Twelve Bar Blues Form_remix.wav"
    assert p.bass_only_m4a == base / "01_The Twelve Bar Blues Form_bass_only.m4a"
    assert p.remix_m4a == base / "01_The Twelve Bar Blues Form_remix.m4a"


def test_slice_suffix_applied():
    p = resolve_paths(
        Path("tracks/BluesBass/01_x.mp3"), slice_spec=SliceSpec(duration=15, start=30)
    )
    assert p.bass.name == "01_x_bass_d15s_s30s.wav"
    assert p.windows.name == "01_x_silence_windows_d15s_s30s.json"
    assert p.remix_m4a.name == "01_x_remix_d15s_s30s.m4a"


def test_custom_out_root():
    p = resolve_paths(Path("tracks/BluesBass/01_x.mp3"), out_root=Path("build"))
    assert p.track_dir == Path("build/BluesBass/01_x")


def test_resolve_paths_render_artifacts():
    from bassify.paths import resolve_paths
    from bassify.slice import SliceSpec

    p = resolve_paths(Path("tracks/Coll/03_Turnarounds.mp3"), slice_spec=SliceSpec(duration=10))
    assert p.render_mp4.name == "03_Turnarounds_render_d10s.mp4"
    assert p.render_still_mp4.name == "03_Turnarounds_render_still_d10s.mp4"
    assert p.thumbnail_png.name == "03_Turnarounds_thumbnail_d10s.png"
    assert p.cover_jpg.name == "03_Turnarounds_cover_d10s.jpg"
