from __future__ import annotations

from bassify.extract import build_filter


def test_build_filter_no_lowpass():
    assert build_filter(None) == "pan=mono|c0=c0-c1"


def test_build_filter_800hz():
    assert build_filter(800) == "pan=mono|c0=c0-c1,lowpass=f=800"


def test_build_filter_500_renders_without_decimal():
    assert build_filter(500.0) == "pan=mono|c0=c0-c1,lowpass=f=500"
