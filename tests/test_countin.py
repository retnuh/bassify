"""Unit tests for the pure logic in bassify.countin.

All tests operate on synthetic lists — NO ffmpeg is invoked.  The ffmpeg
I/O helpers (_envelope, _measure_duration, refine_window_end) are exercised
only at UAT time on real tracks.
"""

from __future__ import annotations

import pytest

from bassify.countin import (
    adaptive_gate,
    filter_onsets,
    is_countin,
    project_cutoff,
    spikes_from_envelope,
)

# ---------------------------------------------------------------------------
# adaptive_gate
# ---------------------------------------------------------------------------


class TestAdaptiveGate:
    def test_median_plus_margin(self):
        # median of [-80, -70, -60, -50, -40] = -60; +12 = -48
        dbs = [-80.0, -70.0, -60.0, -50.0, -40.0]
        assert adaptive_gate(dbs, margin=12.0) == pytest.approx(-48.0)

    def test_empty_list_fallback(self):
        assert adaptive_gate([], margin=12.0) == pytest.approx(-60.0)

    def test_single_element(self):
        assert adaptive_gate([-75.0], margin=12.0) == pytest.approx(-63.0)

    def test_custom_margin(self):
        dbs = [-80.0, -70.0, -60.0]  # median = -70
        assert adaptive_gate(dbs, margin=20.0) == pytest.approx(-50.0)

    def test_all_same(self):
        dbs = [-65.0] * 10  # median = -65
        assert adaptive_gate(dbs, margin=12.0) == pytest.approx(-53.0)

    def test_noisy_floor_raises_gate(self):
        # Quiet window: median -75 → gate -63
        quiet = [-80.0, -78.0, -75.0, -72.0, -70.0]
        gate_quiet = adaptive_gate(quiet, margin=12.0)
        # Noisy window: mic tone shifts floor to -60 → gate -48
        noisy = [-65.0, -62.0, -60.0, -58.0, -55.0]
        gate_noisy = adaptive_gate(noisy, margin=12.0)
        assert gate_noisy > gate_quiet


# ---------------------------------------------------------------------------
# spikes_from_envelope
# ---------------------------------------------------------------------------


class TestSpikesFromEnvelope:
    def _make_rows(self, times_dbs: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return times_dbs

    def test_single_spike(self):
        rows = [(0.0, -80.0), (0.1, -80.0), (0.2, -50.0), (0.3, -50.0), (0.4, -80.0)]
        result = spikes_from_envelope(rows, gate=-60.0)
        assert result == pytest.approx([0.2])

    def test_two_spikes_far_enough_apart(self):
        rows = [
            (0.0, -80.0),
            (0.1, -50.0),  # spike 1
            (0.2, -80.0),
            (0.5, -50.0),  # spike 2 (0.4s later, > 0.15s spacing)
            (0.6, -80.0),
        ]
        result = spikes_from_envelope(rows, gate=-60.0, min_spacing=0.15)
        assert result == pytest.approx([0.1, 0.5])

    def test_two_spikes_too_close_debounced(self):
        # Let's test clean debounce: rises twice within 0.15s — only first is kept
        rows2 = [
            (0.0, -80.0),
            (0.1, -50.0),  # rising edge 1 → onset recorded
            (0.15, -80.0),  # falls below
            (0.2, -50.0),  # rising edge 2 → 0.1s after last onset, < 0.15s spacing → skip
            (0.3, -80.0),
        ]
        result = spikes_from_envelope(rows2, gate=-60.0, min_spacing=0.15)
        assert result == pytest.approx([0.1])

    def test_no_spikes(self):
        rows = [(0.0, -80.0), (0.1, -80.0), (0.2, -80.0)]
        assert spikes_from_envelope(rows, gate=-60.0) == []

    def test_empty_rows(self):
        assert spikes_from_envelope([], gate=-60.0) == []


# ---------------------------------------------------------------------------
# filter_onsets
# ---------------------------------------------------------------------------


class TestFilterOnsets:
    def test_drops_edge_artifact(self):
        # Onset at window_start + 0.05 is within 0.15s margin → dropped
        onsets = [10.05, 10.3, 10.7, 11.1, 11.5, 11.9]
        result = filter_onsets(onsets, window_start=10.0)
        assert result[0] == pytest.approx(10.3)

    def test_keeps_onset_after_margin(self):
        onsets = [10.2, 10.7, 11.2, 11.7, 12.2]
        result = filter_onsets(onsets, window_start=10.0)
        assert 10.2 in result

    def test_drops_trailing_tight_pair(self):
        # Last two onsets 0.1s apart (< 0.30s bass_gap) → last is popped
        onsets = [10.2, 10.8, 11.4, 12.0, 12.6, 12.7]
        result = filter_onsets(onsets, window_start=10.0)
        assert result[-1] == pytest.approx(12.6)
        assert len(result) == 5

    def test_no_trailing_bleed_when_gap_ok(self):
        # All gaps >= 0.30s — nothing popped
        onsets = [10.2, 10.8, 11.4, 12.0, 12.6]
        result = filter_onsets(onsets, window_start=10.0)
        assert len(result) == 5

    def test_cascade_trailing_bleed(self):
        # Last three form a tight cluster: …, 12.0, 12.1, 12.15 → pop twice
        onsets = [10.2, 10.8, 11.4, 12.0, 12.1, 12.15]
        result = filter_onsets(onsets, window_start=10.0)
        assert result[-1] == pytest.approx(12.0)

    def test_does_not_mutate_input(self):
        onsets = [10.2, 10.8, 11.4, 12.0, 12.1]
        original = list(onsets)
        filter_onsets(onsets, window_start=10.0)
        assert onsets == original


# ---------------------------------------------------------------------------
# is_countin
# ---------------------------------------------------------------------------


class TestIsCountin:
    def test_valid_count_in(self):
        # 5 clicks, each 0.5s apart
        onsets = [10.0, 10.5, 11.0, 11.5, 12.0]
        assert is_countin(onsets) is True

    def test_too_few_clicks(self):
        onsets = [10.0, 10.5, 11.0, 11.5]  # only 4
        assert is_countin(onsets) is False

    def test_gap_too_small_fails(self):
        # 5 clicks but one gap < 0.30s
        onsets = [10.0, 10.5, 11.0, 11.5, 11.75]  # last gap 0.25s
        assert is_countin(onsets) is False

    def test_exactly_min_clicks(self):
        onsets = [10.0, 10.5, 11.0, 11.5, 12.0]
        assert is_countin(onsets, min_clicks=5) is True

    def test_more_than_min_clicks(self):
        onsets = [10.0, 10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5]
        assert is_countin(onsets, min_clicks=5) is True

    def test_empty_list(self):
        assert is_countin([]) is False

    def test_single_onset(self):
        assert is_countin([10.0]) is False

    def test_rejects_multi_second_gap(self):
        # 3.39s void between 4th and 5th onset: [1.5, 2.9, 4.3, 5.0, 8.39]
        # gaps: 1.4, 1.4, 0.7, 3.39 — last exceeds max_gap=2.0 → False
        onsets = [1.5, 2.9, 4.3, 5.0, 8.39]
        assert is_countin(onsets) is False

    def test_normal_countin_passes(self):
        # gaps: 1.4, 1.4, 0.65, 0.65, 0.65 — all within [0.30, 2.0] → True
        onsets = [1.5, 2.9, 4.3, 4.95, 5.6, 6.25]
        assert is_countin(onsets) is True

    def test_custom_max_gap(self):
        # gap of 1.8s passes with default max_gap=2.0 but fails with max_gap=1.5
        onsets = [1.0, 2.0, 3.0, 3.5, 5.3]  # last gap 1.8s
        assert is_countin(onsets, max_gap=2.0) is True
        assert is_countin(onsets, max_gap=1.5) is False


# ---------------------------------------------------------------------------
# project_cutoff
# ---------------------------------------------------------------------------


class TestProjectCutoff:
    def test_basic_projection(self):
        onsets = [10.0, 10.5, 11.0, 11.5, 12.0]
        durations = [0.1, 0.1, 0.1, 0.1, 0.1]  # avg5 = 0.1
        # cutoff = 12.0 + 0.1 = 12.1
        result = project_cutoff(onsets, durations, window_start=10.0, window_end=13.0)
        assert result == pytest.approx(12.1)

    def test_uses_only_first_5_durations(self):
        onsets = [10.0, 10.5, 11.0, 11.5, 12.0, 12.5]
        # First 5: [0.1]*5, 6th is outlier 1.0 → should not affect avg
        durations = [0.1, 0.1, 0.1, 0.1, 0.1, 1.0]
        result = project_cutoff(onsets, durations, window_start=10.0, window_end=15.0)
        assert result == pytest.approx(12.6)  # 12.5 + 0.1

    def test_clamped_to_window_end(self):
        onsets = [10.0, 10.5, 11.0, 11.5, 12.0]
        durations = [0.5, 0.5, 0.5, 0.5, 0.5]  # avg5=0.5; cutoff=12.5 > window_end=12.3
        result = project_cutoff(onsets, durations, window_start=10.0, window_end=12.3)
        assert result == pytest.approx(12.3)

    def test_clamped_to_window_start(self):
        # Pathological: last onset == window_start + 0.01, avg dur=0.0
        onsets = [10.0]
        durations = [0.0]
        result = project_cutoff(onsets, durations, window_start=10.0, window_end=15.0)
        assert result == pytest.approx(10.0)

    def test_empty_onsets_returns_window_end(self):
        result = project_cutoff([], [], window_start=10.0, window_end=15.0)
        assert result == pytest.approx(15.0)

    def test_empty_durations_returns_window_end(self):
        result = project_cutoff([12.0], [], window_start=10.0, window_end=15.0)
        assert result == pytest.approx(15.0)

    def test_variable_durations_avg_correct(self):
        onsets = [10.0, 10.6, 11.2, 11.8, 12.4]
        durations = [0.08, 0.12, 0.10, 0.09, 0.11]  # avg = 0.10
        expected = 12.4 + 0.10
        result = project_cutoff(onsets, durations, window_start=10.0, window_end=15.0)
        assert result == pytest.approx(expected, abs=1e-9)
