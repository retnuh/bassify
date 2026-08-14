from __future__ import annotations

from bassify.render.presets import PRESETS, apply_overrides


def test_presets_exist_with_expected_shape():
    assert set(PRESETS) == {"draft", "final", "still"}
    final = PRESETS["final"]
    assert (final.width, final.height, final.fps, final.count) == (1280, 720, 30, 4)
    assert final.labels and final.waveform and final.overlays and not final.still
    assert (round(final.basefreq, 2), round(final.endfreq, 2)) == (65.41, 261.63)
    draft = PRESETS["draft"]
    assert draft.count == 2 and not draft.labels and not draft.waveform
    still = PRESETS["still"]
    assert still.still and still.fps == 2


def test_apply_overrides_res_and_fps():
    p = apply_overrides(PRESETS["final"], res="1920x1080", fps=24)
    assert (p.width, p.height, p.fps) == (1920, 1080, 24)
    assert PRESETS["final"].width == 1280  # original untouched


def test_apply_overrides_flags_and_freq():
    p = apply_overrides(
        PRESETS["final"],
        no_waveform=True,
        no_labels=True,
        freq_range=(40.0, 500.0),
    )
    assert not p.waveform and not p.labels
    assert (p.basefreq, p.endfreq) == (40.0, 500.0)


def test_apply_overrides_none_is_noop():
    assert apply_overrides(PRESETS["final"], fps=None, res=None, count=None) == PRESETS["final"]
