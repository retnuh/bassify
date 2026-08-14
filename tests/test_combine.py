"""Unit tests for bassify.combine pure string-builder functions.

No ffmpeg is invoked; only the expression-builder functions are tested.
"""

from __future__ import annotations

import numpy as np
import pytest

from bassify.combine import (
    apply_donor_splice,
    apply_even_bass_ramp,
    build_bass_duck,
    build_filtergraph,
    build_gate,
    build_original_gate,
)

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


# ---------------------------------------------------------------------------
# apply_donor_splice (pure numpy helper)
# ---------------------------------------------------------------------------


def _ramp(n: int) -> np.ndarray:
    """1-D float64 ramp from 0..1 over n samples."""
    return np.linspace(0.0, 1.0, n)


class TestApplyDonorSplice:
    def test_region_before_last_click_untouched(self):
        """Samples before last_click_sample must not be altered."""
        combined = np.ones(1000, dtype=np.float64)
        donor = np.ones(100, dtype=np.float64)
        result = apply_donor_splice(combined, donor, last_click_sample=500, fade_samples=10)
        np.testing.assert_array_equal(result[:500], combined[:500])

    def test_donor_placed_at_last_click_offset(self):
        """Donor peak lands at last_click_sample (after fade-in)."""
        combined = np.zeros(1000, dtype=np.float64)
        # Flat donor of value 2.0
        donor = np.full(100, 2.0, dtype=np.float64)
        fade = 5
        result = apply_donor_splice(combined, donor, last_click_sample=200, fade_samples=fade)
        # Middle of donor (after fade-in, before fade-out) should be 2.0
        mid = 200 + fade + (100 - 2 * fade) // 2
        assert result[mid] == pytest.approx(2.0)

    def test_donor_starts_with_fade_in(self):
        """First sample of donor region should start near 0 (fade-in applied)."""
        combined = np.zeros(1000, dtype=np.float64)
        donor = np.ones(200, dtype=np.float64)
        fade = 20
        result = apply_donor_splice(combined, donor, last_click_sample=100, fade_samples=fade)
        # First sample of donor = 1.0 * linspace(0,1,20)[0] = 0.0
        assert result[100] == pytest.approx(0.0)

    def test_donor_ends_with_fade_out(self):
        """Last sample of donor region should be near 0 (fade-out applied)."""
        combined = np.zeros(1000, dtype=np.float64)
        donor = np.ones(200, dtype=np.float64)
        fade = 20
        result = apply_donor_splice(combined, donor, last_click_sample=100, fade_samples=fade)
        # Last sample of donor: 1.0 * linspace(1,0,20)[-1] = 0.0
        assert result[100 + 200 - 1] == pytest.approx(0.0)

    def test_region_after_donor_untouched(self):
        """Samples after donor end must not be altered."""
        combined = np.full(1000, 5.0, dtype=np.float64)
        donor = np.zeros(100, dtype=np.float64)
        last = 200
        result = apply_donor_splice(combined, donor, last_click_sample=last, fade_samples=10)
        # After donor: samples [300, 1000) should still be 5.0
        np.testing.assert_array_equal(result[last + len(donor) :], combined[last + len(donor) :])

    def test_combined_not_mutated(self):
        """Input combined array must not be modified in place."""
        combined = np.ones(500, dtype=np.float64)
        original_copy = combined.copy()
        donor = np.ones(50, dtype=np.float64)
        apply_donor_splice(combined, donor, last_click_sample=100, fade_samples=5)
        np.testing.assert_array_equal(combined, original_copy)

    def test_donor_clipped_at_array_end(self):
        """Donor extending past combined end is silently clipped."""
        combined = np.zeros(150, dtype=np.float64)
        donor = np.ones(100, dtype=np.float64)
        # last_click_sample=100, donor=100 -> would end at 200, array only 150 long
        result = apply_donor_splice(combined, donor, last_click_sample=100, fade_samples=5)
        assert len(result) == 150  # length preserved

    # Length-invariant: apply_donor_splice must never change array length
    def test_length_invariant_donor_splice(self):
        """Output length must equal input length regardless of donor size."""
        for n in [100, 1000, 44100]:
            combined = np.random.default_rng(0).random(n)
            donor = np.ones(200, dtype=np.float64)
            result = apply_donor_splice(combined, donor, last_click_sample=50, fade_samples=10)
            assert len(result) == n, f"length changed for n={n}"


# ---------------------------------------------------------------------------
# apply_even_bass_ramp (pure numpy helper)
# ---------------------------------------------------------------------------


class TestApplyEvenBassRamp:
    def test_region_before_last_click_untouched(self):
        """Samples before last_click_sample must not be altered."""
        combined = np.ones(1000, dtype=np.float64)
        bass = np.ones(1000, dtype=np.float64)
        result = apply_even_bass_ramp(combined, bass, last_click_sample=300, bass_onset_sample=700)
        np.testing.assert_array_equal(result[:300], combined[:300])

    def test_region_after_bass_onset_untouched(self):
        """Samples at or after bass_onset_sample must not be altered."""
        combined = np.ones(1000, dtype=np.float64)
        bass = np.ones(1000, dtype=np.float64)
        result = apply_even_bass_ramp(combined, bass, last_click_sample=300, bass_onset_sample=700)
        np.testing.assert_array_equal(result[700:], combined[700:])

    def test_ramp_starts_at_zero_gain(self):
        """First sample of ramp span adds zero (gain=0 at last_click)."""
        combined = np.zeros(1000, dtype=np.float64)
        bass = np.full(1000, 2.0, dtype=np.float64)
        result = apply_even_bass_ramp(combined, bass, last_click_sample=100, bass_onset_sample=500)
        # linspace(0,1,400)[0] = 0.0 -> bass contribution = 2.0 * 0.0 = 0.0
        assert result[100] == pytest.approx(0.0)

    def test_ramp_reaches_full_gain_at_bass_onset(self):
        """Last sample of ramp span adds bass at full gain (gain→1 at bass_onset)."""
        combined = np.zeros(1000, dtype=np.float64)
        bass = np.full(1000, 2.0, dtype=np.float64)
        result = apply_even_bass_ramp(combined, bass, last_click_sample=100, bass_onset_sample=500)
        # linspace(0,1,400)[-1] = 1.0 -> bass contribution = 2.0 * 1.0 = 2.0
        assert result[499] == pytest.approx(2.0)

    def test_additive_on_existing_content(self):
        """Ramp adds onto existing combined content (e.g. donor already present)."""
        combined = np.full(1000, 1.0, dtype=np.float64)
        bass = np.full(1000, 2.0, dtype=np.float64)
        result = apply_even_bass_ramp(combined, bass, last_click_sample=200, bass_onset_sample=600)
        # Mid-point gain ≈ 0.5 -> combined[400] = 1.0 + 2.0*0.5 ≈ 2.0
        mid = 400
        span = 600 - 200
        gain_at_mid = np.linspace(0.0, 1.0, span)[mid - 200]
        assert result[mid] == pytest.approx(1.0 + 2.0 * gain_at_mid)

    def test_combined_not_mutated(self):
        """Input combined array must not be modified in place."""
        combined = np.ones(500, dtype=np.float64)
        original_copy = combined.copy()
        bass = np.ones(500, dtype=np.float64)
        apply_even_bass_ramp(combined, bass, last_click_sample=100, bass_onset_sample=300)
        np.testing.assert_array_equal(combined, original_copy)

    def test_zero_span_no_change(self):
        """last_click_sample == bass_onset_sample produces no change."""
        combined = np.full(500, 3.0, dtype=np.float64)
        bass = np.full(500, 1.0, dtype=np.float64)
        result = apply_even_bass_ramp(combined, bass, last_click_sample=200, bass_onset_sample=200)
        np.testing.assert_array_equal(result, combined)

    # Length-invariant: apply_even_bass_ramp must never change array length
    def test_length_invariant_even_ramp(self):
        """Output length must equal input length."""
        for n in [100, 1000, 44100]:
            rng = np.random.default_rng(1)
            combined = rng.random(n)
            bass = rng.random(n)
            result = apply_even_bass_ramp(combined, bass, last_click_sample=10, bass_onset_sample=n)
            assert len(result) == n, f"length changed for n={n}"


# ---------------------------------------------------------------------------
# build_bass_duck — count-in window variant (hard-zero entire gap)
# ---------------------------------------------------------------------------


class TestBuildBassDuckClickWindow:
    def test_click_window_zeros_entire_gap_to_bass_onset(self):
        """A window with last_click should subtract 1 for the full [start, bass_onset] span."""
        windows = [{"start": 0.0, "end": 6.354, "bass_onset": 6.612, "last_click": 6.211}]
        expr = build_bass_duck(windows, rampup=0.15)
        # Should contain a single -1*between(t, 0, 6.612) term — no rampup split
        assert "-1*between(t,0,6.612)" in expr
        # Should NOT contain the ramp-up denominator for this window
        # (the rampup is only in fallback windows)
        assert "6.462" not in expr  # gap_end = 6.612 - 0.15 would be 6.462

    def test_fallback_window_keeps_ramp(self):
        """A window without last_click still uses the fixed rampup logic."""
        windows = [{"start": 0.0, "end": 6.354, "bass_onset": 6.612}]
        expr = build_bass_duck(windows, rampup=0.15)
        # gap_end = 6.612 - 0.15 = 6.462 present
        assert "6.462" in expr
        assert "6.612" in expr

    def test_mixed_windows_correct_routing(self):
        """Click windows and fallback windows in same list use their respective paths."""
        windows = [
            {"start": 0.0, "end": 6.2, "bass_onset": 6.6, "last_click": 6.0},
            {"start": 20.0, "end": 24.9, "bass_onset": 25.2},
        ]
        expr = build_bass_duck(windows, rampup=0.15)
        # Click window: hard-zero to 6.6
        assert "-1*between(t,0,6.6)" in expr
        # Fallback window: gap_end = 25.2 - 0.15 = 25.05
        assert "25.05" in expr


# ---------------------------------------------------------------------------
# apply_donor_splice — fade guard and ramp-write correctness
# ---------------------------------------------------------------------------


class TestApplyDonorSpliceFadeGuard:
    def test_no_double_fade_when_donor_between_fade_and_2xfade(self):
        """Donor with fade <= len < 2*fade must not apply fades (would overlap/double-modulate)."""
        fade = 10
        # Donor length 15: between fade(10) and 2*fade(20) — fades would overlap.
        donor = np.ones(15, dtype=np.float64)
        combined = np.zeros(200, dtype=np.float64)
        result = apply_donor_splice(combined, donor, last_click_sample=50, fade_samples=fade)
        # All 15 samples should be exactly 1.0 (no fade applied)
        np.testing.assert_array_equal(result[50:65], np.ones(15))

    def test_even_ramp_written_when_donor_too_short_to_splice(self):
        """Even bass ramp must appear in the output even if donor is too short to fade.

        This is a pure simulation of the logic in _apply_donor_splice_to_file:
        apply_even_bass_ramp runs first (modified=True), then donor check fires continue.
        The ramp samples in [last_click, bass_onset] must differ from the zero baseline.
        """
        sr = 1000  # 1 kHz synthetic sr for easy sample arithmetic
        last_click_s = 0.3
        bass_onset_s = 0.9  # 600ms span — analogous to win6's 642ms

        n = 1200
        # Simulate ffmpeg output: combined is all zeros (bass hard-zeroed in gap)
        combined = np.zeros(n, dtype=np.float64)
        # Bass track: constant 1.0 (so ramp = linspace(0,1) * 1.0)
        bass = np.ones(n, dtype=np.float64)

        last_sample = int(last_click_s * sr)
        bass_onset_sample = int(bass_onset_s * sr)

        # Apply even ramp (this is step 1 in _apply_donor_splice_to_file)
        result = apply_even_bass_ramp(combined, bass, last_sample, bass_onset_sample)

        # Ramp region must be non-zero (bass rises from 0 to 1 across the span)
        ramp_region = result[last_sample:bass_onset_sample]
        assert ramp_region.max() > 0.9, "ramp should reach near 1.0 at bass_onset"
        assert ramp_region.min() == pytest.approx(0.0, abs=1e-9), "ramp should start at 0"
        # Samples outside ramp unchanged
        np.testing.assert_array_equal(result[:last_sample], combined[:last_sample])
        np.testing.assert_array_equal(result[bass_onset_sample:], combined[bass_onset_sample:])
