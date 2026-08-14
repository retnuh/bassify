from __future__ import annotations

from pathlib import Path

import yaml


def load_overrides(collection: str, data_dir: Path = Path("data")) -> dict:
    """Return the 'overrides' mapping from data/<collection>.yaml, or {} if absent."""
    path = Path(data_dir) / f"{collection}.yaml"
    if not path.exists():
        return {}
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return {}
    ov = doc.get("overrides") if isinstance(doc, dict) else None
    return ov if isinstance(ov, dict) else {}


def get_override(collection: str, track_stem: str, data_dir: Path = Path("data")) -> dict:
    """Return the override dict for one track stem, or {} if none."""
    entry = load_overrides(collection, data_dir).get(track_stem)
    return entry if isinstance(entry, dict) else {}
