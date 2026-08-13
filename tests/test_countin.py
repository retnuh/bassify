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
        # orig 1.02 is close to bass 1.0 (within tol=0.05) — should be matched
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
        # bass clicks at 1.0, 2.0, 3.0 match orig; 3.4 is guitar-only
        # default margin=0.005: cutoff = 3.4 - 0.005 = 3.395
        bass = [1.0, 2.0, 3.0]
        orig = [1.01, 2.0, 3.0, 3.4]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=4.0)
        assert cutoff == pytest.approx(3.395)

    def test_no_guitar_onset_fallback_to_last_click_plus_0_10(self):
        # all orig onsets match bass — no guitar candidate
        # fallback: min(window_end, last_click+0.10) = min(4.0, 3.0+0.10) = 3.10
        bass = [1.0, 2.0, 3.0]
        orig = [1.01, 2.0, 3.0]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=4.0)
        assert cutoff == pytest.approx(3.10)

    def test_no_guitar_fallback_capped_at_window_end(self):
        # last_click+0.10 > window_end -> capped at window_end
        # last_click=3.0, last_click+0.10=3.10, window_end=3.05 -> fallback=3.05
        bass = [1.0, 2.0, 3.0]
        orig = [1.01, 2.0, 3.0]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=3.05)
        assert cutoff == pytest.approx(3.05)

    def test_cutoff_clamped_to_window_end(self):
        # guitar at 3.9, margin=0.005 -> cutoff=3.895 > window_end=3.8 -> clamped
        bass = [1.0, 2.0, 3.0]
        orig = [1.01, 2.0, 3.0, 3.9]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=3.8)
        assert cutoff == pytest.approx(3.8)

    def test_cutoff_clamped_to_window_start_with_large_margin(self):
        # With explicit margin=0.02: guitar=0.015, cutoff=0.015-0.02=-0.005 < 0.0 -> clamped
        # (With default margin=0.005, clamping to window_start can't occur because
        #  guitar > last_click+0.01 and cutoff=guitar-0.005 > last_click+0.005 >= window_start.)
        bass = []
        orig = [0.015]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=4.0, margin=0.02)
        # last_click=window_start=0.0; 0.015 > 0.01 -> guitar found
        # cutoff = 0.015 - 0.02 = -0.005 -> clamped to 0.0
        assert cutoff == pytest.approx(0.0)

    def test_empty_bass_and_orig(self):
        # No onsets at all: last_click=window_start=0.0, no guitar
        # fallback = min(5.0, 0.0+0.10) = 0.10
        cutoff = find_guitar_cutoff([], [], window_start=0.0, window_end=5.0)
        assert cutoff == pytest.approx(0.10)

    def test_guitar_guard_0_01_rejects_onset_too_close(self):
        # An UNMATCHED onset sits at 3.005, only 0.005 after last_click=3.0 -> NOT selected.
        # Use exact bass/orig matches (tol=0.001) so last_click is precisely 3.0.
        # 3.005 is unmatched at tol=0.001, but 3.005 < 3.0+0.01=3.01 -> skipped.
        # fallback: min(5.0, 3.0+0.10) = 3.10
        bass = [1.0, 2.0, 3.0]
        orig = [1.0, 2.0, 3.0, 3.005]  # exact matches for first three; 3.005 unmatched
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=5.0, tol=0.001)
        # matched=[1.0,2.0,3.0], unmatched=[3.005]; last_click=3.0
        # 3.005 > 3.01? No -> not selected; fallback = min(5.0, 3.10) = 3.10
        assert cutoff == pytest.approx(3.10)

    def test_guitar_guard_0_01_rejects_unmatched_too_close(self):
        # Unmatched onset (far from any bass) at 3.008, only 0.008 after last_click=3.0
        # 3.008 is > 3.0+0.01=3.01? No: 3.008 < 3.01. Must NOT be selected.
        # fallback: min(5.0, 3.0+0.10) = 3.10
        bass = [1.0, 2.0, 3.0]
        # Use tight tol so 3.008 is unmatched:
        orig = [1.01, 2.0, 3.0, 3.008]  # with default tol=0.05, 3.008 matches bass 3.0
        # Make the unmatched case explicit with custom tol:
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=5.0, tol=0.001)
        # tol=0.001: orig 1.01 misses bass 1.0 (diff=0.01 > 0.001),
        # 2.0 matches bass 2.0 exactly, 3.0 matches bass 3.0 exactly, 3.008 is unmatched
        # last_click from matched=[2.0, 3.0] -> 3.0
        # unmatched=[1.01, 3.008]; first unmatched > 3.0+0.01=3.01 -> none (3.008 < 3.01)
        # fallback: min(5.0, 3.0+0.10) = 3.10
        assert cutoff == pytest.approx(3.10)

    def test_guitar_guard_0_01_accepts_unmatched_just_after(self):
        # Companion: unmatched onset at 3.015 is just AFTER last_click+0.01=3.01
        # Should BE selected as guitar
        bass = [1.0, 2.0, 3.0]
        orig = [1.01, 2.0, 3.0, 3.015]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=5.0, tol=0.001)
        # tol=0.001: 1.01 unmatched (diff=0.01), 2.0/3.0 matched, 3.015 unmatched
        # last_click=3.0; first unmatched > 3.01 -> 3.015 (it's > 3.01)
        # cutoff = 3.015 - 0.005 = 3.010
        assert cutoff == pytest.approx(3.010)

    def test_no_bass_onsets_uses_window_start_as_last_click(self):
        # No bass onsets -> last_click = window_start = 0.0
        # orig onsets are all unmatched; first after 0.01 = 0.5
        # cutoff = 0.5 - 0.005 = 0.495 (default margin=0.005)
        bass = []
        orig = [0.5, 1.0]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=5.0)
        assert cutoff == pytest.approx(0.495)

    def test_custom_margin(self):
        bass = [1.0, 2.0, 3.0]
        orig = [1.01, 2.0, 3.0, 3.4]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=4.0, margin=0.05)
        # cutoff = 3.4 - 0.05 = 3.35
        assert cutoff == pytest.approx(3.35)

    def test_custom_tolerance(self):
        # With tol=0.01, orig 1.04 is NOT within 0.01 of bass 1.0 -> unmatched
        bass = [1.0, 2.0, 3.0]
        orig = [1.04, 2.0, 3.0]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=4.0, tol=0.01)
        # 1.04 unmatched; matched=[2.0,3.0] -> last_click=3.0
        # guitar: first unmatched > 3.01 -> none (1.04 < 3.01)
        # fallback: min(4.0, 3.0+0.10) = 3.10
        assert cutoff == pytest.approx(3.10)

    def test_multiple_guitar_onsets_takes_first(self):
        # Two unmatched onsets after last_click; should use the first
        bass = [1.0, 2.0, 3.0]
        orig = [1.01, 2.0, 3.0, 3.4, 3.7]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=4.0)
        # guitar = 3.4 (first unmatched > 3.01); cutoff = 3.4 - 0.005 = 3.395
        assert cutoff == pytest.approx(3.395)

    def test_fallback_when_no_unmatched_after_click(self):
        # orig ends at 3.0 (matched); no unmatched after -> fallback
        # last_click=3.0; fallback = min(5.0, 3.10) = 3.10
        bass = [1.0, 2.0, 3.0]
        orig = [1.01, 2.0, 3.0]
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=5.0)
        assert cutoff == pytest.approx(3.10)

    def test_last_click_from_bass_when_matched_empty(self):
        # No orig onsets match bass -> matched=[], last_click=bass[-1]=3.0
        bass = [1.0, 2.0, 3.0]
        orig = [0.5, 3.2]  # 0.5 far from all bass; 3.2 > 3.0+0.01 and unmatched
        # matched=[], unmatched=[0.5, 3.2]; last_click=bass[-1]=3.0
        # guitar = first unmatched > 3.01 -> 3.2
        # cutoff = 3.2 - 0.005 = 3.195
        cutoff = find_guitar_cutoff(bass, orig, window_start=0.0, window_end=5.0)
        assert cutoff == pytest.approx(3.2 - 0.005)
