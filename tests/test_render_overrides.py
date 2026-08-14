from __future__ import annotations

from pathlib import Path

from bassify.render.overrides import get_override, load_overrides


def _write(dirp: Path, name: str, text: str) -> None:
    dirp.mkdir(parents=True, exist_ok=True)
    (dirp / name).write_text(text)


def test_load_and_get(tmp_path: Path):
    _write(tmp_path, "BluesBass.yaml",
           'overrides:\n  "19_x": {key: F}\n  "27_y": {key: null}\n')
    ov = load_overrides("BluesBass", data_dir=tmp_path)
    assert ov["19_x"]["key"] == "F"
    assert get_override("BluesBass", "19_x", data_dir=tmp_path) == {"key": "F"}
    assert get_override("BluesBass", "27_y", data_dir=tmp_path) == {"key": None}


def test_missing_file_is_empty(tmp_path: Path):
    assert load_overrides("Nope", data_dir=tmp_path) == {}
    assert get_override("Nope", "any", data_dir=tmp_path) == {}


def test_absent_track_is_empty(tmp_path: Path):
    _write(tmp_path, "C.yaml", 'overrides:\n  "01_a": {key: G}\n')
    assert get_override("C", "99_z", data_dir=tmp_path) == {}


def test_empty_yaml_is_empty(tmp_path: Path):
    _write(tmp_path, "C.yaml", "")
    assert load_overrides("C", data_dir=tmp_path) == {}
