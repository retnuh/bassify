"""Unit tests for bassify.combine pure string-builder functions.

No ffmpeg is invoked; only the expression-builder functions are tested.
"""

from __future__ import annotations

from bassify.combine import build_bass_duck, build_filtergraph, build_gate, build_original_gate

# ---------------------------------------------------------------------------
# build_original_gate
# ---------------------------------------------------------------------------


class TestBuildOriginalGate:
    def test_empty_windows_returns_zero(self):
        assert build_original_gate([]) == "0"

    def test_single_window_has_between_and_ramp(self):
        windows = [{"start": 0.0, "end": 6.354}]
        expr = build_original_gate(windows, fade=0.06)
        # Should contain a between() term for the full-gain segment
        assert "between(t" in expr
        # Should contain the fade ramp (cutoff-t)/fade pattern
        assert "6.354" in expr
        # Fade start = 6.354 - 0.06 = 6.294
        assert "6.294" in expr

    def test_two_windows_both_present(self):
        windows = [
            {"start": 0.0, "end": 6.354},
            {"start": 18.486, "end": 24.933},
        ]
        expr = build_original_gate(windows, fade=0.06)
        assert "18.486" in expr
        assert "24.933" in expr
        assert "6.354" in expr

    def test_ramp_formula_structure(self):
        # The ramp term should divide by fade duration (0.06)
        windows = [{"start": 0.0, "end": 1.0}]
        expr = build_original_gate(windows, fade=0.06)
        # ramp: between(t,0.94,1)*(1-t)/0.06  -> denominator 0.06
        assert "0.06" in expr

    def test_full_segment_is_between_term(self):
        windows = [{"start": 2.0, "end": 5.0}]
        expr = build_original_gate(windows, fade=0.1)
        # Full-gain segment: [2.0, 4.9] (5.0 - 0.1)
        assert "between(t,2,4.9)" in expr

    def test_fade_cutoff_in_ramp_term(self):
        windows = [{"start": 0.0, "end": 3.0}]
        expr = build_original_gate(windows, fade=0.06)
        # Fade ramp covers [2.94, 3.0]: formula (3.0-t)/0.06
        assert "between(t,2.94,3)" in expr
        assert "(3-t)" in expr

    def test_eval_frame_not_in_expression(self):
        # The expression itself should be just arithmetic, not include filter options
        windows = [{"start": 0.0, "end": 5.0}]
        expr = build_original_gate(windows)
        assert "eval=frame" not in expr


# ---------------------------------------------------------------------------
# build_bass_duck
# ---------------------------------------------------------------------------


class TestBuildBassDuck:
    def test_empty_windows_returns_one(self):
        assert build_bass_duck([]) == "1"

    def test_single_window_starts_with_one(self):
        windows = [{"start": 0.0, "end": 6.354, "bass_onset": 6.612}]
        expr = build_bass_duck(windows, rampup=0.15)
        assert expr.startswith("1")

    def test_single_window_has_duck_term(self):
        windows = [{"start": 0.0, "end": 6.354, "bass_onset": 6.612}]
        expr = build_bass_duck(windows, rampup=0.15)
        # Gap end = bass_onset - rampup = 6.612 - 0.15 = 6.462
        assert "6.462" in expr
        assert "6.612" in expr
        assert "0.0" in expr or "between(t,0" in expr

    def test_duck_subtracts_one_in_gap(self):
        windows = [{"start": 1.0, "end": 4.0, "bass_onset": 5.0}]
        expr = build_bass_duck(windows, rampup=0.15)
        # Should subtract 1 in gap period [1.0, 4.85]
        assert "-1*between(t,1,4.85)" in expr

    def test_ramp_restores_gain(self):
        windows = [{"start": 1.0, "end": 4.0, "bass_onset": 5.0}]
        expr = build_bass_duck(windows, rampup=0.15)
        # Ramp: -1*between(t,4.85,5)*(1-((t-4.85)/0.15))
        assert "between(t,4.85,5)" in expr
        assert "0.15" in expr

    def test_fallback_when_no_bass_onset_uses_end(self):
        # Old-style window without bass_onset key
        windows = [{"start": 0.0, "end": 5.0}]
        expr = build_bass_duck(windows, rampup=0.15)
        # bass_onset defaults to end=5.0; gap_end = 5.0 - 0.15 = 4.85
        assert "4.85" in expr
        assert "5" in expr

    def test_two_windows_both_gaps_present(self):
        windows = [
            {"start": 0.0, "end": 6.354, "bass_onset": 6.612},
            {"start": 18.486, "end": 24.933, "bass_onset": 25.195},
        ]
        expr = build_bass_duck(windows, rampup=0.15)
        assert "6.612" in expr
        assert "25.195" in expr
        # Two gap-end values: 6.612-0.15=6.462 and 25.195-0.15=25.045
        assert "6.462" in expr
        assert "25.045" in expr

    def test_rampup_denominator_in_expression(self):
        windows = [{"start": 0.0, "end": 4.0, "bass_onset": 5.0}]
        expr = build_bass_duck(windows, rampup=0.15)
        assert "0.15" in expr


# ---------------------------------------------------------------------------
# build_filtergraph (new 3-stream signature)
# ---------------------------------------------------------------------------


class TestBuildFiltergraph:
    def test_contains_required_pieces(self):
        fg = build_filtergraph("between(t,1,2)", "1")
        assert "eval=frame" in fg
        assert "amix=inputs=2:normalize=0" in fg
        assert "[out]" in fg
        assert "pan=mono" in fg

    def test_orig_gate_in_gap_stream(self):
        fg = build_filtergraph("between(t,0,5)", "1")
        assert "between(t,0,5)" in fg
        assert "[gap]" in fg

    def test_bass_duck_in_bd_stream(self):
        fg = build_filtergraph("0", "1-between(t,0,3)")
        assert "1-between(t,0,3)" in fg
        assert "[bd]" in fg

    def test_amix_uses_bd_and_gap(self):
        fg = build_filtergraph("0", "1")
        assert "[bd][gap]amix" in fg


# ---------------------------------------------------------------------------
# build_gate (legacy backward-compat)
# ---------------------------------------------------------------------------


class TestBuildGateLegacy:
    def test_multiple_windows(self):
        windows = [{"start": 12.184, "end": 16.013}, {"start": 47.1, "end": 51.3}]
        assert build_gate(windows) == "between(t,12.184,16.013)+between(t,47.1,51.3)"

    def test_empty_list_returns_zero(self):
        assert build_gate([]) == "0"
