from __future__ import annotations

from pathlib import Path

from bassify.render.metadata import TrackMeta, parse_track_meta


def test_number_from_filename():
    m = parse_track_meta(
        Path("out/C/03_Turnarounds/03_Turnarounds_bass_only.m4a"),
        {"title": "Turnarounds (Bass Only)", "artist": "Ed Friedland"},
    )
    assert m.number == "03"
    assert m.name == "Turnarounds (Bass Only)"
    assert m.artist == "Ed Friedland"


def test_title_tag_beats_filename_slash_case():
    m = parse_track_meta(
        Path("08_Uptown Up_Uptown Down_bass_only.m4a"),
        {"title": "Uptown Up/Uptown Down (Bass Only)", "artist": "Ed Friedland"},
    )
    assert m.name == "Uptown Up/Uptown Down (Bass Only)"
    assert m.number == "08"


def test_missing_title_falls_back_to_filename():
    m = parse_track_meta(Path("05_Some Name_bass_only.m4a"), {"artist": "X"})
    assert m.name == "Some Name"
    assert m.number == "05"


def test_missing_artist_is_none_not_error():
    m = parse_track_meta(Path("01_Foo_bass_only.m4a"), {"title": "Foo (Bass Only)"})
    assert m.artist is None


def test_no_leading_number_is_none():
    m = parse_track_meta(Path("Foo_bass_only.m4a"), {"title": "Foo"})
    assert m.number is None


def test_display_lines_omits_none():
    assert TrackMeta("03", "Turnarounds", "Ed Friedland").display_lines() == [
        "03",
        "Turnarounds",
        "Ed Friedland",
    ]
    assert TrackMeta(None, "Foo", None).display_lines() == ["Foo"]
