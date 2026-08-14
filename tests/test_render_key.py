from __future__ import annotations

from bassify.render.key import resolve_key, root_pc


def test_root_pc_parsing():
    assert root_pc("C") == 0
    assert root_pc("E") == 4
    assert root_pc("F#m") == 6
    assert root_pc("Db") == 1
    assert root_pc("Bm") == 11
    assert root_pc(None) is None
    assert root_pc("") is None
    assert root_pc("H") is None  # not a note


def test_resolve_cli_wins():
    called = []

    def det(p):
        called.append(p)
        return 0

    assert resolve_key("F", {"key": "A"}, "b.wav", _detect=det) == root_pc("F")
    assert called == []  # detector not called


def test_resolve_sidecar_next():
    called = []

    def det(p):
        called.append(p)
        return 0

    assert resolve_key(None, {"key": "A"}, "b.wav", _detect=det) == root_pc("A")
    assert called == []


def test_resolve_sidecar_null_forces_neutral():
    called = []

    def det(p):
        called.append(p)
        return 0

    assert resolve_key(None, {"key": None}, "b.wav", _detect=det) is None
    assert called == []  # explicit null skips detection


def test_resolve_falls_through_to_detect():
    assert resolve_key(None, {}, "b.wav", _detect=lambda p: 7) == 7
