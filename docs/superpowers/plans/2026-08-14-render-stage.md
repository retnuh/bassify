# Render Stage (V2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `bassify render` command — turn a `bass_only.m4a` deliverable (with its co-located `bass.wav`) into a YouTube-ready MP4 (bass-framed showcqt + key-aware note labels + waveform + overlays), a fast still-image video, and an upload thumbnail.

**Architecture:** New `src/bassify/render/` subpackage split by responsibility (metadata, key detection, overrides, labels, thumbnail, waveform, filtergraph, presets, bundled fonts) plus a `render_track()`/`render_batch()` orchestrator. Pure logic (filtergraph string-building, label x-math + tiers, metadata parsing, key precedence, overrides lookup) is unit-tested without ffmpeg; ffmpeg/Pillow/librosa paths are covered by `integration`-marked tests. Render shells out to ffmpeg via the existing `bassify.ffmpeg` helpers, mirroring the audio stages' "build args, run subprocess, return path" pattern.

**Tech Stack:** Python 3.13, typer (CLI), Pillow (new dep — axisfile + thumbnail image generation), PyYAML (new dep — overrides sidecar), librosa (already a dep — reused for Krumhansl key detection), ffmpeg/ffprobe (showcqt, showwavespic, drawtext, overlay, libx264/aac — already required), pytest, ruff, uv, just.

**Reference spec:** `docs/superpowers/specs/2026-08-14-render-design.md` (current source of truth). **Reference prototype:** `experiments/render_proto/` holds proven end-to-end renders on the real `01_The Twelve Bar Blues Form` track — `out_tiers.mp4` (the target look: E root, gold root / white blues-scale / red ♭5 / grey passing, cbrt waveform, 48px labels), plus `gen_axis_tiers.py`, `gen_thumb.py`. The exact ffmpeg/Pillow recipes in the tasks below are copied from these working prototypes.

**Global Constraints:**
- Render's input is the **`bass_only.m4a`** file (or a directory to batch); it NEVER reads the source MP3. The co-located `bass.wav` (same dir, stem `…_bass<sfx>.wav`) drives visuals; the m4a supplies audio + metadata tags + cover art.
- ffmpeg input order is fixed: **input 0 = `bass.wav` (visuals)**, **input 1 = `bass_only.m4a` (audio, `-map 1:a`)**. Additional image inputs (wave.png, cover) follow.
- **Every filtergraph video branch ends `,format=yuv420p`**, and the final output pixel format is pinned `-pix_fmt yuv420p` (a JPEG cover input otherwise yields `yuvj420p`).
- Encoding contract (all video presets): `-c:v libx264 -profile:v high`, `-crf 20` default, `-g` = fps/2 (closed GOP), `-movflags +faststart`, `-c:a aac -b:a 192k -ar 48000`.
- Any `-loop 1` image input MUST be bounded by `-shortest` (else the render never terminates — verified: this is what hung a prototype).
- CQT bass framing default: `basefreq=65.41 endfreq=261.63` (C2–C4). Default resolution **1280×720**, default fps **30**. Waveform strip fixed **80px**, CQT **640px**.
- Waveform PNG uses `showwavespic ... :scale=cbrt` (linear leaves quiet bass a thin band, ~16px of 80; cbrt fills it, ~46px).
- Axis strip is **48px** (`AXIS_H=48`), passed to showcqt as `axis_h=48` so the PNG composites 1:1. PNG must be exactly `width × 48` **RGBA**, note x-positions computed with `x = W·log2(f/basefreq)/log2(endfreq/basefreq)` using the SAME basefreq/endfreq passed to showcqt.
- Label tiers by blues-scale offset from the root pitch-class: **big** = offsets `{0,3,5,7,10}` (root gold+octave, others white), **medium/red** = offset `{6}` (♭5), **small/grey** = the rest. `root_pc=None` → every note big+white (neutral).
- Key precedence: **`--key` > `data/<collection>.yaml` sidecar > librosa auto-detect**. Only the **root** flows into tiers (`Bm` and `B` tier identically). No resolvable key → neutral labels.
- Metadata: track **number** from the filename stem (leading digits before first `_`); **name**/`artist` from the m4a tags; missing fields skipped, never fatal. The `(Bass Only)` title suffix is kept verbatim.
- Fonts: bundled TTF in `src/bassify/render/fonts/` is the default; `--font PATH` overrides. No system-font probing.
- Output artifacts land beside the inputs in `out/<collection>/<track>/`, matching stem + slice suffix: `<track>_render<sfx>.mp4`, `<track>_render_still<sfx>.mp4`, `<track>_thumbnail<sfx>.png`. Intermediates `<track>_axis<sfx>.png`, `<track>_wave<sfx>.png`, `<track>_cover<sfx>.jpg`.

**User decisions (already made):**
- Render works off the **`bass_only.m4a`** (co-located `bass.wav` drives visuals); never reads the source MP3. Directory arg = batch.
- Keep the `(Bass Only)` title suffix verbatim.
- Layout: title + corner logo as **overlays on the CQT** (no title band); playhead is a **line in the waveform** (no separate progress bar).
- Thumbnail: track number / name (bigger) / artist (smaller), centered, ~2/3 down, over full art.
- Axis labels via **axisfile (Pillow)**; **720p30**; presets **draft/final/still**; audio = **bass_only (mono)**; render **standalone**; single-file missing prereq → **error fast**, dir batch → **skip**; **bundled font + `--font`**; perf UX = **slice-first nudge**.
- Prototype-driven refinements: **C2–C4** default range; **cbrt** waveform fill; **48px** strip, big **outlined** text; **key-aware 3-tier** sizing (blues scale, **red ♭5**); key from **`--key` > sidecar > detect**; **optional key** (no key → neutral labels); overrides in **`data/<collection>.yaml`** keyed by stem (already committed, from the course book).
- **Stretch goals (NOT V2):** per-note CQT colors; played-note glow (needs pitch tracking + animated overlay); per-window key detection + multi-key segment stitching (schema left extensible via `segments`).

---

## File Structure

```
src/bassify/render/
  __init__.py      render_track() + render_batch(); public entry points
  metadata.py      TrackMeta + parse_track_meta()
  presets.py       RenderPreset + PRESETS {draft,final,still} + apply_overrides()
  key.py           NOTE_INDEX, parse_key(), root_pc(), detect_key(), resolve_key()
  overrides.py     load_overrides(), get_override()  (reads data/<collection>.yaml)
  labels.py        note_x(), note_tier(), AXIS_H, build_axis_strip()
  thumbnail.py     build_thumbnail()
  waveform.py      render_waveform_pic()  (showwavespic scale=cbrt)
  filtergraph.py   build_full_args(), build_still_args(), WAVE_STRIP_H  (pure)
  fonts/
    DejaVuSansMono.ttf + LICENSE + __init__.py
data/
  BluesBass.yaml   (already committed; overrides keyed by track stem)
tests/
  test_render_metadata.py    (unit)
  test_render_presets.py     (unit)
  test_render_key.py         (unit — parse/precedence; detect is integration)
  test_render_overrides.py   (unit)
  test_render_labels.py      (unit — note_x + note_tier + strip)
  test_render_filtergraph.py (unit)
  test_render_thumbnail.py   (unit)
  test_render_integration.py (integration — ffmpeg + librosa)
```

Modified: `src/bassify/paths.py` (render paths), `src/bassify/cli.py` (`render` command with `--key`), `pyproject.toml` (Pillow + PyYAML deps + font package-data), `justfile` (render passthrough + clean render intermediates).

Task order: dependency/scaffold first, then pure leaves (metadata, presets, key, overrides, labels, filtergraph), then the ffmpeg/Pillow image passes, then the orchestrator, then CLI + integration. Each task is independently committable.

---

### Task 1: Add Pillow + PyYAML deps + bundled font

**Goal:** Add Pillow and PyYAML as runtime dependencies and vendor an open-licensed TTF as a package resource so text rendering is deterministic across platforms.

**Files:**
- Modify: `pyproject.toml`
- Create: `src/bassify/render/__init__.py` (empty stub for now)
- Create: `src/bassify/render/fonts/DejaVuSansMono.ttf` (downloaded)
- Create: `src/bassify/render/fonts/LICENSE`
- Create: `src/bassify/render/fonts/__init__.py` (empty)

**Acceptance Criteria:**
- [ ] `pyproject.toml` `dependencies` includes `pillow>=10` and `pyyaml>=6`.
- [ ] The font ships as wheel package data (force-include).
- [ ] The TTF loads via `PIL.ImageFont.truetype`.
- [ ] `uv sync` succeeds; both `PIL` and `yaml` import.

**Verify:** `uv sync && uv run python -c "import PIL, yaml; from PIL import ImageFont; from importlib.resources import files; ImageFont.truetype(str(files('bassify.render.fonts')/'DejaVuSansMono.ttf'), 12); print('ok')"` → prints `ok`

**Steps:**

- [ ] **Step 1: Add deps.** In `pyproject.toml`:

```toml
dependencies = ["typer>=0.12", "librosa>=0.10", "mutagen>=1.47", "pillow>=10", "pyyaml>=6"]
```

- [ ] **Step 2: Font package data.** In `pyproject.toml`:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/bassify"]

[tool.hatch.build.targets.wheel.force-include]
"src/bassify/render/fonts/DejaVuSansMono.ttf" = "bassify/render/fonts/DejaVuSansMono.ttf"
"src/bassify/render/fonts/LICENSE" = "bassify/render/fonts/LICENSE"
```

- [ ] **Step 3: Download the font.**

```bash
mkdir -p src/bassify/render/fonts
touch src/bassify/render/__init__.py src/bassify/render/fonts/__init__.py
curl -fsSL -o src/bassify/render/fonts/DejaVuSansMono.ttf \
  https://github.com/dejavu-fonts/dejavu-fonts/raw/version_2_37/ttf/DejaVuSansMono.ttf
curl -fsSL -o src/bassify/render/fonts/LICENSE \
  https://github.com/dejavu-fonts/dejavu-fonts/raw/version_2_37/LICENSE
```

If unreachable, DejaVuSansMono.ttf ships with most Linux distros (`/usr/share/fonts/truetype/dejavu/`) and matplotlib; copy it in with a copy of the DejaVu license.

- [ ] **Step 4: Verify.** Run the Verify command above → `ok`.

- [ ] **Step 5: Commit.**

```bash
git add pyproject.toml uv.lock src/bassify/render/
git commit -m "feat(render): add Pillow + PyYAML deps and bundled DejaVu font"
```

---

### Task 2: Metadata parsing

**Goal:** Parse track number (filename), title, and artist (m4a tags) into `TrackMeta`, missing fields skipped not fatal.

**Files:**
- Create: `src/bassify/render/metadata.py`
- Test: `tests/test_render_metadata.py`

**Acceptance Criteria:**
- [ ] `TrackMeta` frozen dataclass: `number|None`, `name|None`, `artist|None`.
- [ ] `parse_track_meta(m4a_path, tags)`: number from leading digits before first `_`; name from `tags["title"]` else filename name portion; artist from `tags["artist"]`.
- [ ] `(Bass Only)` suffix kept verbatim; slash-title case works.
- [ ] Missing title → filename fallback; missing artist → None; no leading number → None. None raise.
- [ ] `display_lines()` returns present fields in order, omitting None.

**Verify:** `uv run pytest tests/test_render_metadata.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_render_metadata.py
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
        "03", "Turnarounds", "Ed Friedland",
    ]
    assert TrackMeta(None, "Foo", None).display_lines() == ["Foo"]
```

- [ ] **Step 2: Run → FAIL** (module not found).

- [ ] **Step 3: Implement.**

```python
# src/bassify/render/metadata.py
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_NUMBER = re.compile(r"^(\d+)_")


def _filename_name(stem: str) -> str | None:
    m = _NUMBER.match(stem)
    rest = stem[m.end() :] if m else stem
    for tail in ("_bass_only", "_bass"):
        idx = rest.find(tail)
        if idx != -1:
            rest = rest[:idx]
            break
    rest = rest.strip()
    return rest.replace("_", " ") if rest else None


@dataclass(frozen=True)
class TrackMeta:
    number: str | None
    name: str | None
    artist: str | None

    def display_lines(self) -> list[str]:
        return [v for v in (self.number, self.name, self.artist) if v]


def parse_track_meta(m4a_path: Path, tags: dict[str, str]) -> TrackMeta:
    """number from filename leading digits; name/artist from tags (name falls back
    to the filename name portion). Missing fields are None; nothing raises."""
    stem = Path(m4a_path).stem
    num_m = _NUMBER.match(stem)
    number = num_m.group(1) if num_m else None
    name = tags.get("title") or _filename_name(stem)
    artist = tags.get("artist") or None
    return TrackMeta(number=number, name=name, artist=artist)
```

- [ ] **Step 4: Run → PASS** (6 tests).

- [ ] **Step 5: Commit.**

```bash
git add src/bassify/render/metadata.py tests/test_render_metadata.py
git commit -m "feat(render): metadata parsing (number from filename, name/artist from tags)"
```

---

### Task 3: Presets and override application

**Goal:** Define the three render presets as immutable knob bundles and patch individual knobs from CLI overrides.

**Files:**
- Create: `src/bassify/render/presets.py`
- Test: `tests/test_render_presets.py`

**Acceptance Criteria:**
- [ ] `RenderPreset` frozen dataclass: `name, width, height, fps, count, labels, waveform, overlays, x264_preset, crf, basefreq, endfreq, still`.
- [ ] `PRESETS` = `draft`/`final`/`still` per the table below.
- [ ] `apply_overrides(preset, **overrides)` returns a new preset with only provided (non-None/True) knobs replaced; `no_waveform=True`→`waveform=False`; `no_labels=True`→`labels=False`; `res="WxH"`→width/height; `freq_range=(lo,hi)`→basefreq/endfreq. Pure (input unchanged).

**Verify:** `uv run pytest tests/test_render_presets.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_render_presets.py
from __future__ import annotations

from bassify.render.presets import PRESETS, apply_overrides


def test_presets_exist_with_expected_shape():
    assert set(PRESETS) == {"draft", "final", "still"}
    final = PRESETS["final"]
    assert (final.width, final.height, final.fps, final.count) == (1280, 720, 30, 4)
    assert final.labels and final.waveform and final.overlays and not final.still
    assert (round(final.basefreq, 2), round(final.endfreq, 2)) == (65.41, 261.63)
    draft = PRESETS["draft"]
    assert draft.count == 2 and not draft.labels and not draft.waveform
    still = PRESETS["still"]
    assert still.still and still.fps == 2


def test_apply_overrides_res_and_fps():
    p = apply_overrides(PRESETS["final"], res="1920x1080", fps=24)
    assert (p.width, p.height, p.fps) == (1920, 1080, 24)
    assert PRESETS["final"].width == 1280  # original untouched


def test_apply_overrides_flags_and_freq():
    p = apply_overrides(PRESETS["final"], no_waveform=True, no_labels=True, freq_range=(40.0, 500.0))
    assert not p.waveform and not p.labels
    assert (p.basefreq, p.endfreq) == (40.0, 500.0)


def test_apply_overrides_none_is_noop():
    assert apply_overrides(PRESETS["final"], fps=None, res=None, count=None) == PRESETS["final"]
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement.**

```python
# src/bassify/render/presets.py
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class RenderPreset:
    name: str
    width: int
    height: int
    fps: int
    count: int
    labels: bool
    waveform: bool
    overlays: bool
    x264_preset: str
    crf: int
    basefreq: float
    endfreq: float
    still: bool


_C2, _C4 = 65.41, 261.63  # default CQT bass framing

PRESETS: dict[str, RenderPreset] = {
    "draft": RenderPreset("draft", 1280, 720, 30, 2, False, False, False,
                          "fast", 20, _C2, _C4, False),
    "final": RenderPreset("final", 1280, 720, 30, 4, True, True, True,
                          "slow", 20, _C2, _C4, False),
    "still": RenderPreset("still", 1280, 720, 2, 0, False, False, False,
                          "ultrafast", 20, _C2, _C4, True),
}


def apply_overrides(
    preset: RenderPreset,
    *,
    res: str | None = None,
    fps: int | None = None,
    count: int | None = None,
    crf: int | None = None,
    freq_range: tuple[float, float] | None = None,
    no_waveform: bool = False,
    no_labels: bool = False,
) -> RenderPreset:
    changes: dict = {}
    if res is not None:
        w, h = res.lower().split("x")
        changes["width"], changes["height"] = int(w), int(h)
    if fps is not None:
        changes["fps"] = fps
    if count is not None:
        changes["count"] = count
    if crf is not None:
        changes["crf"] = crf
    if freq_range is not None:
        changes["basefreq"], changes["endfreq"] = freq_range
    if no_waveform:
        changes["waveform"] = False
    if no_labels:
        changes["labels"] = False
    return replace(preset, **changes)
```

- [ ] **Step 4: Run → PASS** (4 tests).

- [ ] **Step 5: Commit.**

```bash
git add src/bassify/render/presets.py tests/test_render_presets.py
git commit -m "feat(render): preset bundles (draft/final/still) + override application"
```

---

### Task 4: Overrides sidecar reader

**Goal:** Read `data/<collection>.yaml` and look up a per-track override dict by stem; a missing file is not an error.

**Files:**
- Create: `src/bassify/render/overrides.py`
- Test: `tests/test_render_overrides.py`

**Acceptance Criteria:**
- [ ] `load_overrides(collection, data_dir=Path("data"))` returns the `overrides` dict from `data/<collection>.yaml`, or `{}` if the file is absent.
- [ ] `get_override(collection, track_stem, data_dir=...)` returns the dict for that stem, or `{}` if absent.
- [ ] Malformed/empty YAML → `{}` (never raises).

**Verify:** `uv run pytest tests/test_render_overrides.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_render_overrides.py
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
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement.**

```python
# src/bassify/render/overrides.py
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
```

- [ ] **Step 4: Run → PASS** (4 tests).

- [ ] **Step 5: Commit.**

```bash
git add src/bassify/render/overrides.py tests/test_render_overrides.py
git commit -m "feat(render): overrides sidecar reader (data/<collection>.yaml)"
```

---

### Task 5: Key parsing, detection, and resolution

**Goal:** Parse a key string to a root pitch-class, detect a track's key via librosa, and resolve the effective key by precedence (`--key` > sidecar > detect).

**Files:**
- Create: `src/bassify/render/key.py`
- Test: `tests/test_render_key.py` (unit: parse + precedence; `detect_key` is exercised in integration)

**Acceptance Criteria:**
- [ ] `NOTE_INDEX` maps note names (C, C#, Db, …) to pitch-class 0–11 (sharps + common flats).
- [ ] `root_pc(key_str)` returns 0–11 for `"E"`, `"Bm"`, `"F#m"`, `"Db"`; returns None for None/`""`/unparseable.
- [ ] `resolve_key(cli_key, override, bass_wav)` precedence: `cli_key` if given; else if `override` dict has a `"key"` entry (even `None`) use it (sidecar authoritative, `None` → neutral); else `detect_key(bass_wav)`. Returns a root pitch-class or None.
- [ ] `detect_key(bass_wav)` returns a root pitch-class (0–11) via librosa `chroma_cqt` + Krumhansl correlation (integration-tested).
- [ ] `resolve_key` does NOT call `detect_key` when `cli_key` or a sidecar `key` is present (verified by passing a sentinel detector).

**Verify:** `uv run pytest tests/test_render_key.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests.** `resolve_key` takes an optional injectable detector so precedence is testable without librosa/audio.

```python
# tests/test_render_key.py
from __future__ import annotations

from bassify.render.key import root_pc, resolve_key


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
    det = lambda p: called.append(p) or 0
    assert resolve_key("F", {"key": "A"}, "b.wav", _detect=det) == root_pc("F")
    assert called == []  # detector not called


def test_resolve_sidecar_next():
    called = []
    det = lambda p: called.append(p) or 0
    assert resolve_key(None, {"key": "A"}, "b.wav", _detect=det) == root_pc("A")
    assert called == []


def test_resolve_sidecar_null_forces_neutral():
    called = []
    det = lambda p: called.append(p) or 0
    assert resolve_key(None, {"key": None}, "b.wav", _detect=det) is None
    assert called == []  # explicit null skips detection


def test_resolve_falls_through_to_detect():
    assert resolve_key(None, {}, "b.wav", _detect=lambda p: 7) == 7
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement.** Detection uses Krumhansl-Schmuckler over averaged `chroma_cqt`; only the root is returned (major/minor ignored downstream).

```python
# src/bassify/render/key.py
from __future__ import annotations

from pathlib import Path
from typing import Callable

_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
NOTE_INDEX: dict[str, int] = {n: i for i, n in enumerate(_SHARP)}
NOTE_INDEX.update({"Db": 1, "Eb": 3, "Gb": 6, "Ab": 8, "Bb": 10})

# Krumhansl-Schmuckler profiles (used by detect_key).
_MAJ = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MIN = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def root_pc(key_str: str | None) -> int | None:
    """Parse a key string ('E', 'Bm', 'F#m', 'Db') to a root pitch-class 0-11.

    The major/minor suffix is ignored — only the root matters for label tiers.
    Returns None for None/empty/unparseable input.
    """
    if not key_str:
        return None
    s = key_str.strip()
    if s.endswith("m") and not s.endswith("#m") and len(s) > 1 and s[:-1] in NOTE_INDEX:
        s = s[:-1]
    elif s.endswith("m") and len(s) > 2 and s[:-1] in NOTE_INDEX:
        s = s[:-1]
    return NOTE_INDEX.get(s)


def detect_key(bass_wav: Path | str) -> int | None:
    """Detect the root pitch-class of a track via librosa chroma + Krumhansl."""
    import librosa
    import numpy as np

    y, sr = librosa.load(str(bass_wav), mono=True)
    if y.size == 0:
        return None
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)

    def best(profile: list[float]) -> tuple[float, int]:
        p = np.array(profile)
        return max((float(np.corrcoef(np.roll(p, i), chroma)[0, 1]), i) for i in range(12))

    rmaj, imaj = best(_MAJ)
    rmin, imin = best(_MIN)
    return imaj if rmaj >= rmin else imin


def resolve_key(
    cli_key: str | None,
    override: dict,
    bass_wav: Path | str,
    _detect: Callable[[Path | str], int | None] = detect_key,
) -> int | None:
    """Resolve the effective root pitch-class by precedence:
    --key flag > sidecar override (authoritative, incl. explicit null) > auto-detect.
    """
    if cli_key:
        return root_pc(cli_key)
    if "key" in override:  # sidecar entry present (value may be None → neutral)
        return root_pc(override["key"])
    return _detect(bass_wav)
```

- [ ] **Step 4: Run → PASS** (5 tests).

- [ ] **Step 5: Commit.**

```bash
git add src/bassify/render/key.py tests/test_render_key.py
git commit -m "feat(render): key parsing, librosa detection, and precedence resolution"
```

---

### Task 6: Axis-label strip generation (the #1 risk)

**Goal:** Generate the `axisfile` PNG — an exact `width × 48` RGBA strip with key-aware blues-tiered labels at the correct CQT x-positions — and unit-test the x-formula and the tier logic without rendering video.

**Files:**
- Create: `src/bassify/render/labels.py`
- Test: `tests/test_render_labels.py`

**Acceptance Criteria:**
- [ ] `note_x(freq, basefreq, endfreq, width)` = `width*log2(freq/basefreq)/log2(endfreq/basefreq)`; endpoints `0.0` and `width`; one octave up = `width/log2(endfreq/basefreq)`.
- [ ] `AXIS_H == 48`.
- [ ] `note_tier(pc, root_pc)`: root=4 → `note_tier(4,4)="big"`, `(7,4)="big"`, `(10,4)="med"` (♭5), `(5,4)="small"`; `note_tier(pc, None)="big"` for all pc.
- [ ] `build_axis_strip(out_path, width, basefreq, endfreq, root_pc, font_path=None, axis_h=AXIS_H)` writes an exact `width × axis_h` RGBA PNG, alpha-0 background, returns the path; works with `root_pc=None`; no exception when `font_path` is None (uses bundled font).

**Verify:** `uv run pytest tests/test_render_labels.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_render_labels.py
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

from bassify.render.labels import AXIS_H, build_axis_strip, note_tier, note_x

BASE, END, W = 65.41, 261.63, 1280  # C2..C4


def test_note_x_endpoints():
    assert note_x(BASE, BASE, END, W) == 0.0
    assert note_x(END, BASE, END, W) == W


def test_note_x_one_octave():
    assert abs(note_x(2 * BASE, BASE, END, W) - W / math.log2(END / BASE)) < 1e-6


def test_axis_h_is_48():
    assert AXIS_H == 48


def test_note_tier_with_root_E():
    E = 4
    assert note_tier(4, E) == "big"
    assert note_tier(7, E) == "big"    # G = b3
    assert note_tier(9, E) == "big"    # A = 4
    assert note_tier(11, E) == "big"   # B = 5
    assert note_tier(2, E) == "big"    # D = b7
    assert note_tier(10, E) == "med"   # A# = b5
    assert note_tier(5, E) == "small"  # F


def test_note_tier_none_root_all_big():
    for pc in range(12):
        assert note_tier(pc, None) == "big"


def test_build_axis_strip_exact_size_and_rgba(tmp_path: Path):
    out = tmp_path / "axis.png"
    build_axis_strip(out, width=W, basefreq=BASE, endfreq=END, root_pc=4)
    img = Image.open(out)
    assert img.size == (W, AXIS_H) and img.mode == "RGBA"
    assert img.getchannel("A").getextrema()[0] == 0  # transparent bg present


def test_build_axis_strip_keyless_ok(tmp_path: Path):
    out = tmp_path / "axis_neutral.png"
    build_axis_strip(out, width=W, basefreq=BASE, endfreq=END, root_pc=None)
    assert Image.open(out).size == (W, AXIS_H)
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** (from `experiments/render_proto/gen_axis_tiers.py`).

```python
# src/bassify/render/labels.py
from __future__ import annotations

import math
from importlib.resources import files
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

AXIS_H = 48  # fixed strip height; passed to showcqt as axis_h so the PNG maps 1:1

_BLUES_BIG = frozenset({0, 3, 5, 7, 10})  # 1, b3, 4, 5, b7
_FLAT5 = frozenset({6})                    # b5 — blue note (red, medium)

_SIZE = {"big": 30, "med": 24, "small": 16}
_YOFF = {"big": 6, "med": 10, "small": 14}
_GOLD = (255, 215, 0, 255)
_WHITE = (255, 255, 255, 255)
_RED = (255, 60, 60, 255)
_GREY = (150, 150, 150, 220)
_OUTLINE = (0, 0, 0, 255)


def note_x(freq: float, basefreq: float, endfreq: float, width: int) -> float:
    """Screen-x of a frequency in a log2 CQT axis. Matches showcqt's mapping."""
    return width * math.log2(freq / basefreq) / math.log2(endfreq / basefreq)


def note_tier(pitch_class: int, root_pc: int | None) -> str:
    """Blues-scale tier of a pitch class relative to the root.
    root_pc None -> every note 'big' (neutral labels)."""
    if root_pc is None:
        return "big"
    off = (pitch_class - root_pc) % 12
    if off in _FLAT5:
        return "med"
    if off in _BLUES_BIG:
        return "big"
    return "small"


def _midi_freq(midi: int) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _font(font_path: str | None, size: int) -> ImageFont.FreeTypeFont:
    if font_path is None:
        font_path = str(files("bassify.render.fonts") / "DejaVuSansMono.ttf")
    return ImageFont.truetype(font_path, size)


def build_axis_strip(
    out_path: Path,
    width: int,
    basefreq: float,
    endfreq: float,
    root_pc: int | None,
    font_path: str | None = None,
    axis_h: int = AXIS_H,
) -> Path:
    """Write a width×axis_h RGBA axisfile PNG with key-aware tiered note labels.
    Alpha-0 background; black stroke outline for contrast on the bright CQT."""
    out_path = Path(out_path)
    fonts = {t: _font(font_path, s) for t, s in _SIZE.items()}
    img = Image.new("RGBA", (width, axis_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for midi in range(12, 108):  # C0..B7
        f = _midi_freq(midi)
        if f < basefreq or f > endfreq:
            continue
        x = note_x(f, basefreq, endfreq, width)
        pc = midi % 12
        tier = note_tier(pc, root_pc)
        is_root = root_pc is not None and pc == root_pc
        if is_root:
            color = _GOLD
        elif tier == "med":
            color = _RED
        elif tier == "big":
            color = _WHITE
        else:
            color = _GREY
        draw.line([(x, 0), (x, axis_h)], fill=(0, 0, 0, 180), width=3)
        draw.line([(x, 0), (x, axis_h)], fill=color, width=1)
        label = f"{_NAMES[pc]}{midi // 12 - 1}" if is_root else _NAMES[pc]
        draw.text((x + 3, _YOFF[tier]), label, font=fonts[tier], fill=color,
                  stroke_width=3, stroke_fill=_OUTLINE)
    img.save(out_path)
    return out_path
```

- [ ] **Step 4: Run → PASS** (7 tests).

- [ ] **Step 5: Commit.**

```bash
git add src/bassify/render/labels.py tests/test_render_labels.py
git commit -m "feat(render): key-aware tiered axisfile labels (blues scale, red b5)"
```

---

### Task 7: Filtergraph builders (pure args)

**Goal:** Build the ffmpeg arg lists for the full render and the still render as pure functions (no subprocess) so the error-prone graph details are unit-tested.

**Files:**
- Create: `src/bassify/render/filtergraph.py`
- Test: `tests/test_render_filtergraph.py`

**Acceptance Criteria:**
- [ ] `WAVE_STRIP_H == 80`.
- [ ] `build_full_args(preset, *, bass_wav, bass_only, wave_png, cover_png, axis_png, title_file, duration, out_path)` returns a str-only arg list (sans `ffmpeg -hide_banner -y`) that: showcqt with preset basefreq/endfreq/count/fps, `axis_h=48` and `axisfile=<axis_png>` when `preset.labels`; CQT height = `preset.height - WAVE_STRIP_H` when waveform else `preset.height`; stacks CQT over the wave strip when `preset.waveform`; overlays the playhead `x='t/<duration>*<width>'`; logo + drawtext when `preset.overlays`; ends `,format=yuv420p[v]`; `-map "[v]" -map 1:a`; `-shortest`, `-pix_fmt yuv420p`, `-crf`, `-g <fps//2>`, `+faststart`, aac.
- [ ] `preset.waveform` False → no wave input / `vstack`. `preset.labels` False → no `axisfile=`. `preset.overlays` False → no `drawtext`/logo.
- [ ] `build_still_args(preset, *, cover_png, bass_only, out_path)`: `-loop 1`, `-tune stillimage`, scale+pad to preset size, `-c:a copy`, `-shortest`, `-pix_fmt yuv420p`, `+faststart`.
- [ ] The font path is a sentinel the orchestrator substitutes; `filtergraph.py` stays font/fs-agnostic.

**Verify:** `uv run pytest tests/test_render_filtergraph.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_render_filtergraph.py
from __future__ import annotations

from pathlib import Path

from bassify.render.filtergraph import WAVE_STRIP_H, build_full_args, build_still_args
from bassify.render.presets import PRESETS


def _fx(args: list[str]) -> str:
    return args[args.index("-filter_complex") + 1]


def test_wave_strip_height():
    assert WAVE_STRIP_H == 80


def test_full_final_has_all_pieces():
    args = build_full_args(
        PRESETS["final"],
        bass_wav=Path("b.wav"), bass_only=Path("bo.m4a"),
        wave_png=Path("w.png"), cover_png=Path("c.jpg"), axis_png=Path("a.png"),
        title_file=Path("t.txt"), duration=10.0, out_path=Path("o.mp4"),
    )
    assert all(isinstance(a, str) for a in args)
    fx = _fx(args)
    assert "showcqt=" in fx and "basefreq=65.41" in fx and "endfreq=261.63" in fx
    assert "axis_h=48" in fx and "axisfile=a.png" in fx
    assert "1280x640" in fx  # CQT height = 720 - 80
    assert "scale=1280:80" in fx  # wave strip
    assert "t/10.0*1280" in fx    # playhead
    assert "drawtext=" in fx
    assert fx.strip().endswith("format=yuv420p[v]")
    assert "-map" in args and "[v]" in args and "1:a" in args
    assert "-shortest" in args and "-pix_fmt" in args and "yuv420p" in args
    assert "+faststart" in args
    assert args[args.index("-g") + 1] == "15"  # fps 30 -> gop 15


def test_full_draft_drops_labels_waveform_overlays():
    fx = _fx(build_full_args(
        PRESETS["draft"],
        bass_wav=Path("b.wav"), bass_only=Path("bo.m4a"),
        wave_png=None, cover_png=None, axis_png=None,
        title_file=None, duration=10.0, out_path=Path("o.mp4"),
    ))
    assert "axisfile=" not in fx and "drawtext=" not in fx and "vstack" not in fx


def test_still_args_contract():
    args = build_still_args(
        PRESETS["still"], cover_png=Path("c.jpg"),
        bass_only=Path("bo.m4a"), out_path=Path("o.mp4"),
    )
    assert "-loop" in args and "-tune" in args and "stillimage" in args
    assert "-c:a" in args and "copy" in args
    assert "-shortest" in args and "yuv420p" in args and "+faststart" in args
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** (from `experiments/render_proto/` — the proven full-render + still recipes). Input index layout: 0=bass.wav, 1=bass_only, 2=wave.png (if waveform), 3=cover (if overlays).

```python
# src/bassify/render/filtergraph.py
from __future__ import annotations

from pathlib import Path

from bassify.render.presets import RenderPreset

WAVE_STRIP_H = 80  # fixed waveform strip height; CQT takes the rest of preset.height

# Sentinel the orchestrator replaces with the resolved font path (keeps this
# module font/filesystem-agnostic and purely string-building).
FONT_SENTINEL = "__BASSIFY_FONT__"


def _cqt_height(preset: RenderPreset) -> int:
    h = preset.height - (WAVE_STRIP_H if preset.waveform else 0)
    return h - (h % 2)


def _output_args(preset: RenderPreset, out_path: Path) -> list[str]:
    return [
        "-c:v", "libx264", "-profile:v", "high",
        "-preset", preset.x264_preset, "-crf", str(preset.crf),
        "-pix_fmt", "yuv420p", "-r", str(preset.fps), "-g", str(preset.fps // 2),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", "-shortest", str(out_path),
    ]


def build_full_args(
    preset: RenderPreset,
    *,
    bass_wav: Path,
    bass_only: Path,
    wave_png: Path | None,
    cover_png: Path | None,
    axis_png: Path | None,
    title_file: Path | None,
    duration: float,
    out_path: Path,
) -> list[str]:
    w = preset.width
    cqt_h = _cqt_height(preset)
    axis = (
        f":axis_h=48:axisfile={axis_png}"
        if (preset.labels and axis_png is not None)
        else ""
    )
    parts = [
        f"[0:a]showcqt=s={w}x{cqt_h}:fps={preset.fps}:count={preset.count}:"
        f"basefreq={preset.basefreq:g}:endfreq={preset.endfreq:g}:"
        f"bar_g=2:sono_g=3:bar_v=30:sono_v=30:tc=0.17{axis},format=yuv420p[cqt]"
    ]
    inputs = ["-i", str(bass_wav), "-i", str(bass_only)]
    next_idx = 2
    video = "[cqt]"

    if preset.waveform and wave_png is not None:
        idx = next_idx
        next_idx += 1
        inputs += ["-loop", "1", "-i", str(wave_png)]
        parts.append(f"[{idx}:v]scale={w}:{WAVE_STRIP_H},format=yuva420p[wavebg]")
        parts.append(f"color=c=red:s=2x{WAVE_STRIP_H}:r={preset.fps}[ph]")
        parts.append(f"[wavebg][ph]overlay=x='t/{duration}*{w}':y=0:shortest=1[wave]")
        parts.append("[cqt][wave]vstack[stacked]")
        video = "[stacked]"

    if preset.overlays and cover_png is not None:
        idx = next_idx
        next_idx += 1
        inputs += ["-i", str(cover_png)]
        parts.append(f"[{idx}:v]scale=80:-1[logo]")
        parts.append(f"{video}[logo]overlay=x=10:y=10[withlogo]")
        video = "[withlogo]"

    if preset.overlays and title_file is not None:
        parts.append(
            f"{video}drawtext=textfile={title_file}:fontfile={FONT_SENTINEL}:"
            f"fontsize=28:fontcolor=white:x=100:y=15:"
            f"box=1:boxcolor=black@0.5:boxborderw=8,format=yuv420p[v]"
        )
    else:
        parts.append(f"{video}format=yuv420p[v]")

    return [
        *inputs,
        "-filter_complex", ";".join(parts),
        "-map", "[v]", "-map", "1:a",
        *_output_args(preset, out_path),
    ]


def build_still_args(
    preset: RenderPreset,
    *,
    cover_png: Path,
    bass_only: Path,
    out_path: Path,
) -> list[str]:
    w, h = preset.width, preset.height
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
    )
    return [
        "-loop", "1", "-i", str(cover_png),
        "-i", str(bass_only),
        "-vf", vf,
        "-c:v", "libx264", "-tune", "stillimage",
        "-preset", preset.x264_preset, "-crf", str(preset.crf),
        "-pix_fmt", "yuv420p", "-r", str(preset.fps),
        "-c:a", "copy", "-shortest", "-movflags", "+faststart",
        str(out_path),
    ]
```

- [ ] **Step 4: Run → PASS** (4 tests).

- [ ] **Step 5: Commit.**

```bash
git add src/bassify/render/filtergraph.py tests/test_render_filtergraph.py
git commit -m "feat(render): pure filtergraph builders for full + still renders"
```

---

### Task 8: Waveform + thumbnail generators

**Goal:** Implement the whole-track waveform PNG (ffmpeg `showwavespic scale=cbrt`) and the burned-in thumbnail (Pillow).

**Files:**
- Create: `src/bassify/render/waveform.py`
- Create: `src/bassify/render/thumbnail.py`
- Test: `tests/test_render_thumbnail.py` (thumbnail; waveform proven in Task 10 integration)

**Acceptance Criteria:**
- [ ] `render_waveform_pic(bass_wav, out_png, width, height=WAVE_STRIP_H, color="cyan")` runs ffmpeg `showwavespic=...:scale=cbrt`, returns the PNG path; raises `FfmpegError` on failure.
- [ ] `build_thumbnail(cover_png, out_png, meta, font_path, width=1280, height=720)` writes a `width×height` PNG: full art scaled to fill, a semi-transparent scrim behind the text block, `meta.display_lines()` centered and anchored ~2/3 down at descending sizes; missing lines absent; returns the path; never raises on missing number/artist.

**Verify:** `uv run pytest tests/test_render_thumbnail.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write the failing thumbnail tests.**

```python
# tests/test_render_thumbnail.py
from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from PIL import Image

from bassify.render.metadata import TrackMeta
from bassify.render.thumbnail import build_thumbnail


def _art(path: Path) -> None:
    Image.new("RGB", (400, 400), (30, 30, 30)).save(path)


def _font() -> str:
    return str(files("bassify.render.fonts") / "DejaVuSansMono.ttf")


def test_thumbnail_size(tmp_path: Path):
    art = tmp_path / "cover.jpg"
    _art(art)
    out = tmp_path / "thumb.png"
    build_thumbnail(art, out, TrackMeta("03", "Turnarounds", "Ed Friedland"), _font())
    assert Image.open(out).size == (1280, 720)


def test_thumbnail_missing_lines_ok(tmp_path: Path):
    art = tmp_path / "cover.jpg"
    _art(art)
    out = tmp_path / "thumb.png"
    build_thumbnail(art, out, TrackMeta(None, "Foo", None), _font())
    assert out.exists()
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement waveform.py.**

```python
# src/bassify/render/waveform.py
from __future__ import annotations

from pathlib import Path

from bassify.ffmpeg import run_ffmpeg
from bassify.render.filtergraph import WAVE_STRIP_H


def render_waveform_pic(
    bass_wav: Path,
    out_png: Path,
    width: int,
    height: int = WAVE_STRIP_H,
    color: str = "cyan",
) -> Path:
    """Whole-track showwavespic PNG. scale=cbrt fills the strip with quiet bass
    (linear leaves ~16px of 80; cbrt ~46px)."""
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-i", str(bass_wav),
        "-filter_complex", f"showwavespic=s={width}x{height}:colors={color}:scale=cbrt",
        "-frames:v", "1", "-update", "1", str(out_png),
    ])
    return out_png
```

- [ ] **Step 4: Implement thumbnail.py** (from `experiments/render_proto/gen_thumb.py`).

```python
# src/bassify/render/thumbnail.py
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from bassify.render.metadata import TrackMeta

_SIZES = (48, 72, 40)  # number / name / artist


def build_thumbnail(
    cover_png: Path,
    out_png: Path,
    meta: TrackMeta,
    font_path: str,
    width: int = 1280,
    height: int = 720,
) -> Path:
    """Full art + burned track number/name/artist, centered, ~2/3 down."""
    out_png = Path(out_png)
    art = Image.open(cover_png).convert("RGB").resize((width, height))
    draw = ImageDraw.Draw(art, "RGBA")

    lines = meta.display_lines()
    sizes = [72] if len(lines) == 1 else list(_SIZES)
    fonts = [ImageFont.truetype(font_path, sizes[i]) for i in range(len(lines))]
    heights = [draw.textbbox((0, 0), t, font=f)[3] - draw.textbbox((0, 0), t, font=f)[1]
               for t, f in zip(lines, fonts)]

    gap = 16
    y = int(height * 0.62)
    draw.rectangle([0, y - 20, width, height], fill=(0, 0, 0, 120))
    for text, font, h in zip(lines, fonts, heights):
        bb = draw.textbbox((0, 0), text, font=font)
        draw.text(((width - (bb[2] - bb[0])) / 2, y), text, font=font,
                  fill=(255, 255, 255, 255))
        y += h + gap

    art.save(out_png)
    return out_png
```

- [ ] **Step 5: Run → PASS** (2 tests).

- [ ] **Step 6: Commit.**

```bash
git add src/bassify/render/waveform.py src/bassify/render/thumbnail.py tests/test_render_thumbnail.py
git commit -m "feat(render): waveform pre-pass (cbrt) + burned-in thumbnail"
```

---

### Task 9: Paths + orchestrator (render_track + render_batch)

**Goal:** Add render output paths to `Paths`, and implement `render_track()` (resolve inputs from a `bass_only.m4a`, read tags + cover, resolve key, run needed pre-passes, run ffmpeg, always produce the thumbnail) plus `render_batch()`.

**Files:**
- Modify: `src/bassify/paths.py`
- Create: `src/bassify/render/__init__.py` (replace Task 1 stub)
- Test: `tests/test_paths.py` (extend); orchestrator proven in Task 10 integration.

**Acceptance Criteria:**
- [ ] `Paths` gains `render_mp4, render_still_mp4, thumbnail_png, axis_png, wave_png, cover_jpg`; `resolve_paths` emits them with the `<track>_<kind><sfx>.<ext>` convention.
- [ ] `resolve_render_inputs(bass_only_m4a)` returns the co-located `bass.wav` (stem `_bass_only`→`_bass`), raising `FileNotFoundError` with a clear message if absent.
- [ ] `render_track(bass_only_m4a, preset_name="final", *, key=None, res=None, fps=None, count=None, crf=None, freq_range=None, no_waveform=False, no_labels=False, font=None, force=False)`: reads tags + cover from the m4a; parses `TrackMeta`; resolves key (`--key` > sidecar for `<collection>`/stem > detect on bass.wav); runs only the pre-passes the preset needs; substitutes the font sentinel; runs the correct ffmpeg (full or still); always writes the thumbnail; returns the primary output path.
- [ ] `render_batch(directory, **kwargs)` renders every `*_bass_only*.m4a` under `directory` that has a co-located `bass.wav`, skips the rest with a one-line summary, per-track try/except.
- [ ] Collection for the sidecar lookup = the m4a's grandparent dir name (`out/<collection>/<track>/x.m4a` → `<collection>`).

**Verify:** `uv run pytest tests/test_paths.py -v` → pass

**Steps:**

- [ ] **Step 1: Extend Paths + failing path test.** In `src/bassify/paths.py` add fields to `Paths` and emit in `resolve_paths`:

```python
    render_mp4: Path
    render_still_mp4: Path
    thumbnail_png: Path
    axis_png: Path
    wave_png: Path
    cover_jpg: Path
```

```python
        render_mp4=name("render", "mp4"),
        render_still_mp4=name("render_still", "mp4"),
        thumbnail_png=name("thumbnail", "png"),
        axis_png=name("axis", "png"),
        wave_png=name("wave", "png"),
        cover_jpg=name("cover", "jpg"),
```

Add to `tests/test_paths.py`:

```python
def test_resolve_paths_render_artifacts():
    from bassify.paths import resolve_paths
    from bassify.slice import SliceSpec

    p = resolve_paths(Path("tracks/Coll/03_Turnarounds.mp3"), slice_spec=SliceSpec(duration=10))
    assert p.render_mp4.name == "03_Turnarounds_render_d10s.mp4"
    assert p.render_still_mp4.name == "03_Turnarounds_render_still_d10s.mp4"
    assert p.thumbnail_png.name == "03_Turnarounds_thumbnail_d10s.png"
    assert p.cover_jpg.name == "03_Turnarounds_cover_d10s.jpg"
```

Run `uv run pytest tests/test_paths.py -v` → FAIL then PASS after the edit.

- [ ] **Step 2: Implement the orchestrator.**

```python
# src/bassify/render/__init__.py
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from bassify.ffmpeg import ffprobe_duration, run_ffmpeg
from bassify.render.filtergraph import FONT_SENTINEL, build_full_args, build_still_args
from bassify.render.key import resolve_key
from bassify.render.labels import build_axis_strip
from bassify.render.metadata import parse_track_meta
from bassify.render.overrides import get_override
from bassify.render.presets import PRESETS, apply_overrides
from bassify.render.thumbnail import build_thumbnail
from bassify.render.waveform import render_waveform_pic


def _read_tags(m4a: Path) -> dict[str, str]:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format_tags=title,artist",
           "-of", "default=noprint_wrappers=1", str(m4a)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    tags: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if line.startswith("TAG:title="):
            tags["title"] = line[len("TAG:title=") :]
        elif line.startswith("TAG:artist="):
            tags["artist"] = line[len("TAG:artist=") :]
    return tags


def _extract_cover(m4a: Path, dest: Path) -> bool:
    cmd = ["ffmpeg", "-hide_banner", "-y", "-i", str(m4a), "-an", "-c:v", "copy", str(dest)]
    proc = subprocess.run(cmd, capture_output=True)
    return proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0


def _resolve_font(font: str | None) -> str:
    from importlib.resources import files

    return font or str(files("bassify.render.fonts") / "DejaVuSansMono.ttf")


def resolve_render_inputs(bass_only_m4a: Path) -> Path:
    """Return the co-located bass.wav for a bass_only.m4a, or raise if missing."""
    bass_only_m4a = Path(bass_only_m4a)
    bass_wav = bass_only_m4a.with_name(
        bass_only_m4a.stem.replace("_bass_only", "_bass") + ".wav"
    )
    if not bass_wav.exists():
        raise FileNotFoundError(
            f"No bass.wav alongside {bass_only_m4a}. Run 'bassify run' first "
            f"(or 'bassify extract <dir>' to regenerate bass.wav). Expected: {bass_wav}"
        )
    return bass_wav


def _out_paths(bass_only_m4a: Path) -> dict[str, Path]:
    d = bass_only_m4a.parent
    base = bass_only_m4a.stem.replace("_bass_only", "")  # keeps slice suffix
    def p(kind: str, ext: str) -> Path:
        return d / f"{base}_{kind}.{ext}"
    return {
        "render": p("render", "mp4"), "still": p("render_still", "mp4"),
        "thumb": p("thumbnail", "png"), "axis": p("axis", "png"),
        "wave": p("wave", "png"), "cover": p("cover", "jpg"),
    }


def render_track(
    bass_only_m4a: Path,
    preset_name: str = "final",
    *,
    key: str | None = None,
    res: str | None = None,
    fps: int | None = None,
    count: int | None = None,
    crf: int | None = None,
    freq_range: tuple[float, float] | None = None,
    no_waveform: bool = False,
    no_labels: bool = False,
    font: str | None = None,
    force: bool = False,
) -> Path:
    """Render one bass_only.m4a to video (+ thumbnail). Returns the primary output."""
    bass_only_m4a = Path(bass_only_m4a)
    bass_wav = resolve_render_inputs(bass_only_m4a)
    preset = apply_overrides(
        PRESETS[preset_name], res=res, fps=fps, count=count, crf=crf,
        freq_range=freq_range, no_waveform=no_waveform, no_labels=no_labels,
    )
    out = _out_paths(bass_only_m4a)
    font_path = _resolve_font(font)

    tags = _read_tags(bass_only_m4a)
    meta = parse_track_meta(bass_only_m4a, tags)

    # Cover art: needed for thumbnail (always) + logo/still. Synthesize if absent.
    if not _extract_cover(bass_only_m4a, out["cover"]):
        from PIL import Image

        Image.new("RGB", (1280, 720), (20, 20, 20)).save(out["cover"])
    build_thumbnail(out["cover"], out["thumb"], meta, font_path)

    if preset.still:
        run_ffmpeg(build_still_args(
            preset, cover_png=out["cover"], bass_only=bass_only_m4a, out_path=out["still"]))
        return out["still"]

    # Resolve key (collection = grandparent dir name), then label tiers.
    collection = bass_only_m4a.parent.parent.name
    override = get_override(collection, _source_stem(bass_only_m4a))
    root_pc = resolve_key(key, override, bass_wav)

    axis_png = None
    if preset.labels:
        axis_png = build_axis_strip(
            out["axis"], width=preset.width, basefreq=preset.basefreq,
            endfreq=preset.endfreq, root_pc=root_pc, font_path=font_path,
        )
    wave_png = render_waveform_pic(bass_wav, out["wave"], width=preset.width) \
        if preset.waveform else None

    duration = ffprobe_duration(bass_only_m4a)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
        tf.write(" ".join(x for x in (meta.number, meta.name) if x))
        title_file = Path(tf.name)

    args = build_full_args(
        preset, bass_wav=bass_wav, bass_only=bass_only_m4a,
        wave_png=wave_png, cover_png=out["cover"] if preset.overlays else None,
        axis_png=axis_png, title_file=title_file if preset.overlays else None,
        duration=duration, out_path=out["render"],
    )
    args = [a.replace(FONT_SENTINEL, font_path) for a in args]
    run_ffmpeg(args)
    title_file.unlink(missing_ok=True)
    return out["render"]


def _source_stem(bass_only_m4a: Path) -> str:
    """The source track stem for sidecar lookup: strip _bass_only and any slice
    suffix so 'out/C/03_X/03_X_bass_only_d10s.m4a' -> '03_X'."""
    from bassify.slice import SliceSpec

    stem = bass_only_m4a.stem.replace("_bass_only", "")
    sfx = SliceSpec.from_filename(bass_only_m4a).suffix()
    if sfx and stem.endswith(sfx):
        stem = stem[: -len(sfx)]
    return stem


def render_batch(directory: Path, **kwargs) -> None:
    """Render every *_bass_only*.m4a under directory that has a co-located bass.wav."""
    directory = Path(directory)
    m4as = sorted(directory.rglob("*_bass_only*.m4a"))
    rendered = skipped = failed = 0
    for m in m4as:
        bass_wav = m.with_name(m.stem.replace("_bass_only", "_bass") + ".wav")
        if not bass_wav.exists():
            skipped += 1
            continue
        try:
            render_track(m, **kwargs)
            rendered += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR rendering {m.name}: {exc}")
            failed += 1
    print(f"render batch done: {rendered} rendered, {skipped} skipped (no bass.wav), {failed} failed")
```

- [ ] **Step 3: Run** `uv run pytest tests/test_paths.py -v` → PASS.

- [ ] **Step 4: Commit.**

```bash
git add src/bassify/paths.py src/bassify/render/__init__.py tests/test_paths.py
git commit -m "feat(render): render paths + render_track/render_batch orchestrator"
```

---

### Task 10: CLI command + integration tests (end-to-end gate)

**Goal:** Wire `bassify render` into the CLI (single file → error fast; directory → batch; `--key` + all knob flags; slice-first nudge), add the justfile passthrough + clean, and prove the whole thing end-to-end with ffmpeg + librosa.

**USER-ORDERED GATE — NON-SKIPPABLE.** This task was requested by the user in the current conversation ("prove the whole thing end-to-end"). It MUST NOT be closed by walking around it, by declaring it "verified inline", or by substituting a cheaper check. Close only after every item in acceptanceCriteria has been re-validated independently, with output captured.

**Files:**
- Modify: `src/bassify/cli.py`
- Modify: `justfile`
- Test: `tests/test_render_integration.py`

**Acceptance Criteria:**
- [ ] `bassify render <bass_only.m4a|dir>` exists with `--preset, --duration, --start, --res, --fps, --count, --crf, --freq-range (two floats), --key, --no-waveform, --no-labels, --font, --force`.
- [ ] Single-file arg with no co-located `bass.wav` exits non-zero with the "No bass.wav alongside …" message.
- [ ] Directory arg → `render_batch` (skips m4as without bass.wav, prints summary).
- [ ] `--preset final` with no `--duration`/`--start` prints a slice-first tip before rendering.
- [ ] `just render <args>` works; `just clean` also removes `*_axis.png`, `*_wave.png`, `*_cover.jpg`.
- [ ] Integration test (on the synthetic tagged source): `still` and sliced `final` render to valid MP4s (video+audio, yuv420p), thumbnail is 1280×720, video-duration == audio-duration; the error-fast case raises; `detect_key` returns a pitch-class on a single-pitch source.

**Verify:** `uv run pytest tests/test_render_integration.py -v` → pass; then `just check` → green.

**Steps:**

- [ ] **Step 1: Write the failing integration test.**

```python
# tests/test_render_integration.py
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from bassify.paths import resolve_paths
from bassify.pipeline import run_pipeline
from bassify.render import render_track
from bassify.render.key import detect_key
from bassify.slice import SliceSpec

pytestmark = pytest.mark.integration
ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
skip_reason = "ffmpeg/ffprobe not on PATH"


def _probe(path: Path, entries: str) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", entries,
         "-of", "default=noprint_wrappers=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout


def _make_tagged_source(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-y",
         "-f", "lavfi", "-i", "sine=frequency=80:duration=1",
         "-f", "lavfi", "-i", "anoisesrc=d=1:c=pink:a=0.05",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo:d=1.5",
         "-f", "lavfi", "-i", "sine=frequency=80:duration=1",
         "-f", "lavfi", "-i", "anoisesrc=d=1:c=pink:a=0.05",
         "-filter_complex",
         "[0:a][1:a]amix=inputs=2:normalize=0[aL];[1:a]acopy[aR];"
         "[aL][aR]join=inputs=2:channel_layout=stereo[segA];"
         "[2:a]acopy[segB];"
         "[3:a][4:a]amix=inputs=2:normalize=0[cL];[4:a]acopy[cR];"
         "[cL][cR]join=inputs=2:channel_layout=stereo[segC];"
         "[segA][segB][segC]concat=n=3:v=0:a=1[out]",
         "-map", "[out]", "-metadata", "title=Test Track (Bass Only)",
         "-metadata", "artist=Tester", "-c:a", "pcm_s16le", str(path)],
        check=True, capture_output=True,
    )


@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
def test_render_still_and_final(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "Coll" / "01_Test.wav"
    _make_tagged_source(src)

    spec = SliceSpec(duration=3)
    run_pipeline(src, slice_spec=spec, force=True)
    bass_only = resolve_paths(src, slice_spec=spec).bass_only_m4a
    assert bass_only.exists()

    still = render_track(bass_only, "still", force=True)
    assert still.exists() and "audio" in _probe(still, "stream=codec_type")

    out = render_track(bass_only, "final", force=True)
    assert out.exists()
    ct = _probe(out, "stream=codec_type")
    assert "video" in ct and "audio" in ct
    assert "yuv420p" in _probe(out, "stream=pix_fmt")

    from PIL import Image
    thumb = out.with_name(out.name.replace("_render", "_thumbnail").replace(".mp4", ".png"))
    assert thumb.exists() and Image.open(thumb).size == (1280, 720)

    vdur = float(_probe(out, "format=duration").split("=")[1])
    adur = float(_probe(bass_only, "format=duration").split("=")[1])
    assert abs(vdur - adur) < 0.5


@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
def test_detect_key_returns_pitch_class(tmp_path: Path) -> None:
    src = tmp_path / "e2.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=82.41:duration=4", "-c:a", "pcm_s16le", str(src)],
                   check=True, capture_output=True)
    pc = detect_key(src)
    assert pc is None or 0 <= pc <= 11


@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
def test_render_errors_without_bass_wav(tmp_path: Path) -> None:
    lonely = tmp_path / "99_Nope_bass_only.m4a"
    lonely.write_bytes(b"not really an m4a")
    with pytest.raises(FileNotFoundError):
        render_track(lonely, "still")
```

- [ ] **Step 2: Run → the render tests may already pass** once Tasks 1-9 are done (they import `render_track` directly). Confirm, then wire the CLI (needed for the `just`/manual path + the CLI ACs).

- [ ] **Step 3: Wire the CLI.** In `src/bassify/cli.py`:

```python
from bassify.render import render_batch, render_track
```

```python
@app.command()
def render(
    input_path: Path,
    preset: Annotated[str, typer.Option("--preset", help="draft | final | still")] = "final",
    duration: DurationOpt = None,
    start: StartOpt = None,
    res: Annotated[str | None, typer.Option("--res", help="Override resolution, e.g. 1920x1080.")] = None,
    fps: Annotated[int | None, typer.Option("--fps", help="Override frames per second.")] = None,
    count: Annotated[int | None, typer.Option("--count", help="CQT transforms per frame (smoothness).")] = None,
    crf: Annotated[int | None, typer.Option("--crf", help="x264 quality (lower=better, default 20).")] = None,
    freq_range: Annotated[
        tuple[float, float] | None,
        typer.Option("--freq-range", help="CQT bass framing LOW HIGH in Hz (default 65.41 261.63 = C2-C4)."),
    ] = None,
    key: Annotated[
        str | None,
        typer.Option("--key", help="Force key for label tiers (e.g. F, Bm). Wins over sidecar + auto-detect."),
    ] = None,
    no_waveform: Annotated[bool, typer.Option("--no-waveform", help="Drop the waveform strip.")] = False,
    no_labels: Annotated[bool, typer.Option("--no-labels", help="Drop the note-name axis labels.")] = False,
    font: Annotated[Path | None, typer.Option("--font", help="Override the label/title TTF.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing output.")] = False,
) -> None:
    """Render a bass_only.m4a to video (CQT + waveform) plus a thumbnail.

    Pass a single bass_only.m4a (the co-located bass.wav drives the visuals),
    or a directory to batch every bass_only.m4a that has a bass.wav beside it.
    """
    if preset not in ("draft", "final", "still"):
        raise typer.BadParameter("preset must be draft, final, or still")
    kwargs = dict(
        preset_name=preset, key=key, res=res, fps=fps, count=count, crf=crf,
        freq_range=freq_range, no_waveform=no_waveform, no_labels=no_labels,
        font=str(font) if font else None, force=force,
    )
    if input_path.is_dir():
        render_batch(input_path, **kwargs)
        return
    if preset == "final" and duration is None and start is None:
        print("note: full-length render can take minutes (CQT is ~0.5-2x realtime).")
        print("tip: add --duration 30 to preview a slice first.")
    render_track(input_path, **kwargs)
```

Note: `--duration`/`--start` select which sliced `bass_only.m4a` you point at (the slice suffix is in the filename) and drive the nudge; render reads the effective slice from the filename. Locating a sliced sibling automatically is out of scope.

- [ ] **Step 4: justfile passthrough + clean.** Add after the `encode` passthrough:

```make
render *ARGS:
    uv run bassify render {{ARGS}}
```

Extend the `clean` recipe body (after the `*.wav` delete):

```bash
    find "{{dir}}" -type f \( -name '*_axis.png' -o -name '*_wave.png' -o -name '*_cover.jpg' \) -delete
```

- [ ] **Step 5: Run integration + full gate.**

Run: `uv run pytest tests/test_render_integration.py -v` → PASS
Run: `just check` → lint + fmt-check + all tests green

- [ ] **Step 6: Commit.**

```bash
git add src/bassify/cli.py justfile tests/test_render_integration.py
git commit -m "feat(render): CLI render command (+--key) + integration tests + just wiring"
```

---

## Self-Review

**1. Spec coverage:**
- §1 scope (render command, 3 presets, thumbnail, key-aware axisfile labels, overrides sidecar, dir batch, slice, bundled font) → Tasks 1-10. ✓
- §2 inputs/prereqs (m4a-centric, co-located bass.wav, error-fast vs skip, clean) → Task 9 `resolve_render_inputs` + Task 10 CLI/integration. ✓
- §3 architecture (9-module subpackage, pure vs ffmpeg split, data flow incl. key step) → Tasks 2-9. ✓
- §4 layout (48px key-aware strip, cbrt waveform, overlays, playhead) → Tasks 6/7/8 (proto-proven). ✓
- §5 metadata → Task 2. ✓
- §6 thumbnail → Task 8. ✓
- §7 presets + §7a key-aware labels + §7b overrides → Tasks 3, 6, 4, 5. ✓
- §8 CLI (arg = m4a/dir, flags incl. `--key`, standalone, prereq handling, artifacts) → Task 10. ✓
- §9 fonts → Task 1 + `_resolve_font`. ✓
- §10 perf UX (slice-first nudge) → Task 10. Live progress = ffmpeg's own default; `run_ffmpeg` captures stderr and prints on failure — see note below. ✓ (nudge), partial (streamed progress).
- §11 testing → Tasks 2-10 (seven test files). ✓
- §12 deps (Pillow, PyYAML, librosa reuse) → Task 1 + Task 5. ✓

**2. Placeholder scan:** No TBD/TODO. `FONT_SENTINEL` is a real defined constant with an explicit substitution step in Task 9.

**3. Type consistency:** `TrackMeta` (T2) → T8, T9. `RenderPreset`/`apply_overrides` (T3) → T7, T9. `get_override` (T4) → T9. `resolve_key`/`root_pc`/`detect_key` (T5) → T9. `note_tier`/`build_axis_strip`/`AXIS_H` (T6) → T9. `build_full_args`/`build_still_args`/`WAVE_STRIP_H`/`FONT_SENTINEL` (T7) → T9 + T8. `render_waveform_pic`/`build_thumbnail` (T8) → T9. `render_track`/`render_batch` (T9) → T10. Paths fields consistent with `resolve_paths`. Signatures match across tasks.

**Note on progress UX (§10):** `run_ffmpeg` captures stderr and prints only on failure, so live `frame=…` progress is not streamed. The plan implements the slice-first nudge (the more valuable half) and keeps `run_ffmpeg` unchanged to avoid destabilizing the audio stages. Live progress is a small optional follow-up to `bassify.ffmpeg`, out of scope here.
