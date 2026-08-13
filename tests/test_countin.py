"""Unit tests for the pure logic in bassify.countin.

All tests operate on synthetic onset lists — NO librosa or ffmpeg is invoked.
The I/O layer (refine_window) is exercised only at UAT time on real tracks.
"""

from __future__ import annotations

import pytest

from bassify.countin import find_guitar_cutoff, match_onsets

# ---------------------------------------------------------------------------
# match_onsets
# ---------------------------------------------------------------------------


class TestMatchOnsets:
    def test_all_matched(self):
        bass = [1.0, 2.0, 3.0]
        orig = [1.01, 2.0, 3.0]
        matched, unmatched = match_onsets(bass, orig)
        assert matched == pytest.approx([1.01, 2.0, 3.0])
        assert unmatched == []

    def test_some_unmatched(self):
        bass = [1.0, 2.0, 3.0]
        orig = [1.01, 2.0, 3.0, 3.4]
        matched, unmatched = match_onsets(bass, orig)
        assert matched == pytest.approx([1.01, 2.0, 3.0])
        assert unmatched == pytest.approx([3.4])

    def test_none_matched(self):
        bass = [5.0, 6.0]
        orig = [1.0, 2.0, 3.0]
        matched, unmatched = match_onsets(bass, orig)
        assert matched == []
        assert unmatched == pytest.approx([1.0, 2.0, 3.0])

    def test_empty_bass(self):
        orig = [1.0, 2.0, 3.0]
        matched, unmatched = match_onsets([], orig)
        assert matched == []
        assert unmatched == pytest.approx([1.0, 2.0, 3.0])

    def test_empty_orig(self):
        bass = [1.0, 2.0]
        matched, unmatched = match_onsets(bass, [])
        assert matched == []
        assert unmatched == []

    def test_both_empty(self):
        matched, unmatched = match_onsets([], [])
        assert matched == []
        assert unmatched == []

    def test_custom_tolerance_tight(self):
        bass = [1.0]
        orig = [1.04]  # within default tol=0.05
        matched, unmatched = match_onsets(bass, orig, tol=0.05)
        assert matched == pytest.approx([1.04])
        assert unmatched == []

    def test_custom_tolerance_miss(self):
        bass = [1.0]
        orig = [1.06]  # outside tol=0.05
        matched, unmatched = match_onsets(bass, orig, tol=0.05)
        assert matched == []
        assert unmatched == pytest.approx([1.06])

    def test_order_preserved(self):
        bass = [1.0, 3.0]
        orig = [1.01, 2.5, 3.02]
        matched, unmatched = match_onsets(bass, orig)
        assert matched == pytest.approx([1.01, 3.02])
        assert unmatched == pytest.approx([2.5])

    def test_multiple_bass_onsets_can_match_same_orig(self):
        # orig 1.02 is close to both bass 1.0 and bass 1.05 — should be matched
        # orig 2.0 is not near any bass onset -> unmatched
        bass = [1.0, 1.05]
        orig = [1.02, 2.0]
        matched, unmatched = match_onsets(bass, orig)
        assert matched == pytest.approx([1.02])
        assert unmatched == pytest.approx([2.0])


# ---------------------------------------------------------------------------
# find_guitar_cutoff
# ---------------------------------------------------------------------------


class TestFindGuitarCutoff:
    def test_basic_guitar_found(self):
        # bass clicks at 1.0, 2.0, 3.0 match orig onsets; 3.4 is guitar-only
        bass = [1.0, 2.0, 3.0]
        orig = [1.01, 2.0, 3.0, 3.4]
        # last_click = 3.0, guitar = 3.4, cutoff = 3.4 - 0.02 = 3.38
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=4.0)
        assert cutoff == pytest.approx(3.38)

    def test_no_guitar_onset_fallback_to_window_end(self):
        # all orig onsets match bass — no guitar candidate
        bass = [1.0, 2.0, 3.0]
        orig = [1.01, 2.0, 3.0]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=4.0)
        assert cutoff == pytest.approx(4.0)

    def test_cutoff_clamped_to_window_end(self):
        # guitar at 3.9 with window_end=3.8: cutoff=3.88 > window_end -> clamped
        bass = [1.0, 2.0, 3.0]
        orig = [1.01, 2.0, 3.0, 3.9]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=3.8)
        assert cutoff == pytest.approx(3.8)

    def test_cutoff_clamped_to_window_start(self):
        # guitar at 0.015 -> cutoff = -0.005 -> clamped to window_start=0.0
        bass = []
        orig = [0.015]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=4.0)
        # last_click = window_start = 0.0; guitar=0.015 > 0.0+0.01=0.01 -> matched
        # cutoff = 0.015 - 0.02 = -0.005 -> clamped to 0.0
        assert cutoff == pytest.approx(0.0)

    def test_empty_bass_and_orig(self):
        cutoff = find_guitar_cutoff([], [], window_start=0.0, window_end=5.0)
        assert cutoff == pytest.approx(5.0)

    def test_guitar_must_be_after_last_click_plus_0_01(self):
        # orig has 3.005 which is only 0.005 after last_click=3.0 -> not guitar
        bass = [1.0, 2.0, 3.0]
        orig = [1.01, 2.0, 3.0, 3.005]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=5.0)
        # 3.005 is within tol=0.05 of bass 3.0 -> matched, not guitar
        # after filtering matched/unmatched: unmatched=[] -> fallback
        assert cutoff == pytest.approx(5.0)

    def test_no_bass_onsets_uses_window_start_as_last_click(self):
        # No bass onsets -> last_click = window_start = 0.0
        # orig onsets: 0.5 (guitar since no bass match) with 0.5 > 0.0+0.01
        bass = []
        orig = [0.5, 1.0]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=5.0)
        # first unmatched after 0.01 from window_start = 0.5
        # cutoff = 0.5 - 0.02 = 0.48
        assert cutoff == pytest.approx(0.48)

    def test_custom_margin(self):
        bass = [1.0, 2.0, 3.0]
        orig = [1.01, 2.0, 3.0, 3.4]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=4.0, margin=0.05)
        # cutoff = 3.4 - 0.05 = 3.35
        assert cutoff == pytest.approx(3.35)

    def test_custom_tolerance(self):
        # With tol=0.01, orig 1.04 is NOT within 0.01 of bass 1.0 -> unmatched (guitar)
        bass = [1.0, 2.0, 3.0]
        orig = [1.04, 2.0, 3.0]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=4.0, tol=0.01)
        # 1.04 not matched -> unmatched=[1.04]; last_click from matched=[2.0,3.0] -> 3.0
        # guitar must be > 3.0+0.01=3.01; 1.04 < 3.01 -> no guitar -> fallback
        assert cutoff == pytest.approx(4.0)

    def test_multiple_guitar_onsets_takes_first(self):
        # Two unmatched onsets after last_click; should use the first
        bass = [1.0, 2.0, 3.0]
        orig = [1.01, 2.0, 3.0, 3.4, 3.7]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=4.0)
        # guitar = 3.4 (first unmatched after last_click=3.0+0.01)
        assert cutoff == pytest.approx(3.38)

    def test_fallback_when_bass_onsets_but_no_orig_unmatched_after_click(self):
        # orig ends at 3.0 (matched); no unmatched after -> fallback
        bass = [1.0, 2.0, 3.0]
        orig = [1.01, 2.0, 3.0]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=5.0)
        assert cutoff == pytest.approx(5.0)

    def test_last_click_from_bass_when_matched_empty(self):
        # No orig onsets match bass -> matched=[], fallback to bass[-1]=3.0
        bass = [1.0, 2.0, 3.0]
        orig = [0.5, 3.2]  # 0.5 not near any bass; 3.2 > 3.0+0.01 but matched=[]
        # matched=[], unmatched=[0.5, 3.2]; last_click=bass[-1]=3.0
        # guitar = first unmatched > 3.0+0.01 = 3.01 -> 3.2
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=5.0)
        assert cutoff == pytest.approx(3.2 - 0.02)
