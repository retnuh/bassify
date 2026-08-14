from __future__ import annotations

from pathlib import Path

from bassify.render.filtergraph import WAVE_STRIP_H, build_full_args, build_still_args
from bassify.render.presets import PRESETS


def _fx(args: list[str]) -> str:
    return args[args.index("-filter_complex") + 1]


def test_wave_strip_height():
    assert WAVE_STRIP_H == 80


def test_full_final_has_all_pieces():
    args = build_full_args(
        PRESETS["final"],
        bass_wav=Path("b.wav"),
        bass_only=Path("bo.m4a"),
        wave_png=Path("w.png"),
        cover_png=Path("c.jpg"),
        axis_png=Path("a.png"),
        title_file=Path("t.txt"),
        duration=10.0,
        out_path=Path("o.mp4"),
    )
    assert all(isinstance(a, str) for a in args)
    fx = _fx(args)
    assert "showcqt=" in fx and "basefreq=65.41" in fx and "endfreq=261.63" in fx
    assert "axis_h=48" in fx and "axisfile=a.png" in fx
    assert "1280x640" in fx  # CQT height = 720 - 80
    assert "scale=1280:80" in fx  # wave strip
    assert "t/10.0*1280" in fx  # playhead
    assert "drawtext=" in fx
    assert fx.strip().endswith("format=yuv420p[v]")
    assert "-map" in args and "[v]" in args and "1:a" in args
    assert "-shortest" in args and "-pix_fmt" in args and "yuv420p" in args
    assert "+faststart" in args
    assert args[args.index("-g") + 1] == "15"  # fps 30 -> gop 15


def test_full_draft_drops_labels_waveform_overlays():
    fx = _fx(
        build_full_args(
            PRESETS["draft"],
            bass_wav=Path("b.wav"),
            bass_only=Path("bo.m4a"),
            wave_png=None,
            cover_png=None,
            axis_png=None,
            title_file=None,
            duration=10.0,
            out_path=Path("o.mp4"),
        )
    )
    assert "axisfile=" not in fx and "drawtext=" not in fx and "vstack" not in fx


def test_full_no_waveform_but_overlays_cover_index_2():
    from bassify.render.presets import apply_overrides

    preset = apply_overrides(PRESETS["final"], no_waveform=True)  # overlays still True
    args = build_full_args(
        preset,
        bass_wav=Path("b.wav"),
        bass_only=Path("bo.m4a"),
        wave_png=None,
        cover_png=Path("c.jpg"),
        axis_png=Path("a.png"),
        title_file=Path("t.txt"),
        duration=10.0,
        out_path=Path("o.mp4"),
    )
    fx = args[args.index("-filter_complex") + 1]
    # no waveform strip / vstack
    assert "vstack" not in fx and "scale=1280:80" not in fx
    # cover is input index 2 (bass=0, bass_only=1, no wave, cover=2)
    assert "[2:v]scale=80" in fx  # logo scale references index 2, not 3
    # CQT fills full height (no strip subtracted) — 720, not 640
    assert "1280x720" in fx


def test_still_args_contract():
    args = build_still_args(
        PRESETS["still"],
        cover_png=Path("c.jpg"),
        bass_only=Path("bo.m4a"),
        out_path=Path("o.mp4"),
    )
    assert "-loop" in args and "-tune" in args and "stillimage" in args
    assert "-c:a" in args and "copy" in args
    assert "-shortest" in args and "yuv420p" in args and "+faststart" in args
