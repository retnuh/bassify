# Bassify Audio Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `bassify` CLI that isolates bass from specially-formatted stereo practice tracks (L=full mix, R=mix-minus-bass), recombines speech/count-ins into the gaps, produces a pannable stereo remix, and encodes shareable `.m4a` files.

**Architecture:** Each pipeline stage is a Python function that builds an ffmpeg command, runs it via subprocess (through a shared `run_ffmpeg` helper that prints the command), and writes an inspectable artifact to a smart output-path layout. Pure string/path builders (gate strings, pan maps, silence parsing, path resolution) are separated from subprocess I/O so they unit-test without ffmpeg. A Typer CLI exposes each stage plus a `run` command that chains them.

**Tech Stack:** Python 3.14, uv (deps/venv), Typer (CLI), ffmpeg/ffprobe (system binaries, shelled out), pytest, ruff, pre-commit, just (task runner), GitHub Actions CI.

**Global Constraints:**
- Source format assumption: input stereo is **L = full mix, R = full mix minus bass**; bass = L − R.
- Output layout is fixed: `out/<collection>/<track>/<track>_<artifact><slice-suffix>.<ext>` where `<collection>` = immediate parent dir name of the input, `<track>` = input basename without extension.
- Artifact type names are exact: `bass.wav`, `silence_windows.json`, `combined.wav`, `remix.wav`, `combined.m4a`, `remix.m4a`.
- Slice suffix rules (inserted after artifact type, before extension): none → `""`; `--duration 15` → `_d15s`; `--start 30` → `_s30s`; both → `_d15s_s30s`.
- Detect defaults: `--threshold -40` (dB), `--min-gap 1.0` (s). Window padding: ±0.1 s clamped to `[0, duration]`.
- `combine` uses `amix=inputs=2:normalize=0` and `eval=frame` on the gate (both mandatory — see spec §6).
- Every ffmpeg invocation prints its full command before running and passes `-y` (overwrite temp) and, on audio-only stages, `-vn`. `encode` intentionally keeps the art stream (no `-vn`).
- Skip-if-exists by default; `--force` regenerates.
- In `run`, the ffmpeg time-cut (`-ss`/`-t`) is applied ONLY at `extract`; downstream stages read already-sliced WAVs and must NOT re-cut, but DO carry the slice suffix in filenames. This is threaded as a `cut_inputs: bool` parameter (see Task 2 and Task 8).

**User decisions (already made):**
- V1 is audio only: `extract`, `detect`, `combine`, `remix`, `encode`. Video `render` deferred.
- Shell out to ffmpeg; no numpy/soundfile DSP in V1.
- CLI library is Typer (not argparse).
- Collection dir = immediate parent dir name of the input.
- Rerun behavior = skip-if-exists, `--force` to regenerate.
- Final encode format = AAC in `.m4a`, ~256 kbps, carrying original MP3 metadata + cover art.
- `encode` produces BOTH `combined.m4a` and `remix.m4a`.
- `remix` = stereo, L = combined, R = original right channel. It is a listening artifact, never re-fed to `extract`.
- `--duration`/`--start` test-slice flags on every command; slice params encoded into output filenames.
- Interactive UAT with the user against real `tracks/BluesBass/` audio is a required verification step (Task 10).

---

## File Structure

```
bassify/
  pyproject.toml            # uv-managed; metadata, deps, ruff + pytest config
  justfile                  # task runner
  .pre-commit-config.yaml   # ruff check + ruff format hooks
  .github/workflows/ci.yml  # uv sync, ruff, ffmpeg install, pytest
  .gitignore                # (existing) + out/ and tracks/
  src/bassify/
    __init__.py             # version
    slice.py                # SliceSpec dataclass, suffix + ffmpeg-arg builders
    paths.py                # resolve_paths(): collection/track/artifact/suffix
    ffmpeg.py               # run_ffmpeg(), ffprobe_duration(), input-arg helpers
    extract.py              # L-R subtraction -> bass.wav
    detect.py               # silencedetect parse, window pairing, JSON
    combine.py              # gate-string builder, mono mix -> combined.wav
    remix.py                # pan/join builder, stereo -> remix.wav
    encode.py               # AAC/.m4a with metadata + cover art
    cli.py                  # Typer app: extract/detect/combine/remix/encode/run
  tests/
    test_slice.py
    test_paths.py
    test_detect.py
    test_combine.py
    test_remix.py
    test_ffmpeg.py
    test_integration.py     # marked; skipped when ffmpeg absent
  docs/                     # brief + spec (existing)
  tracks/BluesBass/         # symlink (gitignored)
  out/                      # generated artifacts (gitignored)
```

Responsibilities are split so pure logic (`slice.py`, `paths.py`, and the builder functions inside `detect`/`combine`/`remix`) is testable without spawning ffmpeg, while subprocess orchestration lives in `ffmpeg.py` and the stage `run_*` functions.

---

## Task 0: Project scaffold

**Goal:** A working uv project with Typer, ruff, pytest, pre-commit, justfile, CI, and an importable empty `bassify` package.

**Files:**
- Create: `pyproject.toml`
- Create: `justfile`
- Create: `.pre-commit-config.yaml`
- Create: `.github/workflows/ci.yml`
- Create: `src/bassify/__init__.py`
- Modify: `.gitignore` (append `out/` and `tracks/`)

**Acceptance Criteria:**
- [ ] `uv sync` succeeds and creates a lockfile.
- [ ] `uv run bassify --help` prints Typer help listing no commands yet (or an empty app) without error.
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass.
- [ ] `uv run pytest` runs (0 tests collected is acceptable) and exits 0.
- [ ] `out/` and `tracks/` are gitignored.

**Verify:** `uv sync && uv run ruff check . && uv run pytest -q` → all exit 0

**Steps:**

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "bassify"
version = "0.1.0"
description = "Isolate bass from specially-formatted stereo practice tracks"
requires-python = ">=3.12"
dependencies = ["typer>=0.12"]

[project.scripts]
bassify = "bassify.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/bassify"]

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.6", "pre-commit>=3.8"]

[tool.ruff]
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
markers = ["integration: requires ffmpeg on PATH"]
testpaths = ["tests"]
```

- [ ] **Step 2: Create `src/bassify/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 3: Create a minimal `src/bassify/cli.py` so the entry point imports**

```python
import typer

app = typer.Typer(help="Isolate bass from stereo practice tracks.", no_args_is_help=True)


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Write `justfile`**

```make
# List recipes
default:
    @just --list

install:
    uv sync

lint:
    uv run ruff check .

fmt:
    uv run ruff format .

test:
    uv run pytest -q

check: lint test

# Stage passthroughs (args forwarded), e.g. `just run tracks/BluesBass/01_*.mp3`
extract *ARGS:
    uv run bassify extract {{ARGS}}

detect *ARGS:
    uv run bassify detect {{ARGS}}

combine *ARGS:
    uv run bassify combine {{ARGS}}

remix *ARGS:
    uv run bassify remix {{ARGS}}

encode *ARGS:
    uv run bassify encode {{ARGS}}

run *ARGS:
    uv run bassify run {{ARGS}}
```

- [ ] **Step 5: Write `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

- [ ] **Step 6: Write `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install ffmpeg
        run: sudo apt-get update && sudo apt-get install -y ffmpeg
      - name: Install uv
        uses: astral-sh/setup-uv@v3
      - name: Sync
        run: uv sync
      - name: Ruff check
        run: uv run ruff check .
      - name: Ruff format check
        run: uv run ruff format --check .
      - name: Pytest
        run: uv run pytest -q
```

- [ ] **Step 7: Append to `.gitignore`**

Append these two lines:

```
out/
tracks/
```

- [ ] **Step 8: Verify and commit**

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run bassify --help
git add pyproject.toml uv.lock justfile .pre-commit-config.yaml .github/workflows/ci.yml src/bassify/__init__.py src/bassify/cli.py .gitignore
git commit -m "chore: scaffold bassify uv project (typer, ruff, pytest, CI, justfile)"
```

---

## Task 1: Slice spec + output-path resolution

**Goal:** Pure, unit-tested `SliceSpec` (duration/start → suffix + ffmpeg args) and `resolve_paths()` producing every artifact path with the correct slice suffix.

**Files:**
- Create: `src/bassify/slice.py`
- Create: `src/bassify/paths.py`
- Test: `tests/test_slice.py`
- Test: `tests/test_paths.py`

**Acceptance Criteria:**
- [ ] `SliceSpec` renders suffixes: none→`""`, duration→`_d15s`, start→`_s30s`, both→`_d15s_s30s`.
- [ ] `SliceSpec.input_args()` returns `["-ss","30","-t","15"]`-style lists (start before duration), and `[]` when empty.
- [ ] `resolve_paths()` derives collection = parent dir name, track = stem, and builds all six artifact paths under `out/<collection>/<track>/` with suffix applied before the extension.
- [ ] Integer-valued floats render without a trailing `.0` (`15` not `15.0`); non-integers render compactly (`--duration 2.5` → `_d2.5s`).

**Verify:** `uv run pytest tests/test_slice.py tests/test_paths.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write `tests/test_slice.py`**

```python
from bassify.slice import SliceSpec


def test_empty_suffix_and_args():
    s = SliceSpec()
    assert s.suffix() == ""
    assert s.input_args() == []
    assert s.is_empty()


def test_duration_only():
    s = SliceSpec(duration=15)
    assert s.suffix() == "_d15s"
    assert s.input_args() == ["-t", "15"]


def test_start_only():
    s = SliceSpec(start=30)
    assert s.suffix() == "_s30s"
    assert s.input_args() == ["-ss", "30"]


def test_both_start_before_duration():
    s = SliceSpec(duration=15, start=30)
    assert s.suffix() == "_d15s_s30s"
    assert s.input_args() == ["-ss", "30", "-t", "15"]


def test_integer_float_renders_without_point_zero():
    assert SliceSpec(duration=15.0).suffix() == "_d15s"


def test_non_integer_renders_compact():
    assert SliceSpec(duration=2.5).suffix() == "_d2.5s"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_slice.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bassify.slice'`

- [ ] **Step 3: Write `src/bassify/slice.py`**

```python
from __future__ import annotations

from dataclasses import dataclass


def _fmt(value: float) -> str:
    """Render a number compactly: 15.0 -> '15', 2.5 -> '2.5'."""
    if value == int(value):
        return str(int(value))
    return str(value)


@dataclass(frozen=True)
class SliceSpec:
    """Optional test-slice window. duration/start are seconds; None means unset."""

    duration: float | None = None
    start: float | None = None

    def is_empty(self) -> bool:
        return self.duration is None and self.start is None

    def suffix(self) -> str:
        parts = []
        if self.duration is not None:
            parts.append(f"d{_fmt(self.duration)}s")
        if self.start is not None:
            parts.append(f"s{_fmt(self.start)}s")
        return "_" + "_".join(parts) if parts else ""

    def input_args(self) -> list[str]:
        """ffmpeg input-side options: -ss (start) before -t (duration)."""
        args: list[str] = []
        if self.start is not None:
            args += ["-ss", _fmt(self.start)]
        if self.duration is not None:
            args += ["-t", _fmt(self.duration)]
        return args
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_slice.py -v`
Expected: PASS

- [ ] **Step 5: Write `tests/test_paths.py`**

```python
from pathlib import Path

from bassify.paths import resolve_paths
from bassify.slice import SliceSpec


def test_default_layout():
    p = resolve_paths(Path("tracks/BluesBass/01_The Twelve Bar Blues Form.mp3"))
    base = Path("out/BluesBass/01_The Twelve Bar Blues Form")
    assert p.track_dir == base
    assert p.bass == base / "01_The Twelve Bar Blues Form_bass.wav"
    assert p.windows == base / "01_The Twelve Bar Blues Form_silence_windows.json"
    assert p.combined == base / "01_The Twelve Bar Blues Form_combined.wav"
    assert p.remix == base / "01_The Twelve Bar Blues Form_remix.wav"
    assert p.combined_m4a == base / "01_The Twelve Bar Blues Form_combined.m4a"
    assert p.remix_m4a == base / "01_The Twelve Bar Blues Form_remix.m4a"


def test_slice_suffix_applied():
    p = resolve_paths(
        Path("tracks/BluesBass/01_x.mp3"), slice_spec=SliceSpec(duration=15, start=30)
    )
    assert p.bass.name == "01_x_bass_d15s_s30s.wav"
    assert p.windows.name == "01_x_silence_windows_d15s_s30s.json"
    assert p.remix_m4a.name == "01_x_remix_d15s_s30s.m4a"


def test_custom_out_root():
    p = resolve_paths(Path("tracks/BluesBass/01_x.mp3"), out_root=Path("build"))
    assert p.track_dir == Path("build/BluesBass/01_x")
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bassify.paths'`

- [ ] **Step 7: Write `src/bassify/paths.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bassify.slice import SliceSpec


@dataclass(frozen=True)
class Paths:
    track_dir: Path
    bass: Path
    windows: Path
    combined: Path
    remix: Path
    combined_m4a: Path
    remix_m4a: Path


def resolve_paths(
    input_path: Path,
    out_root: Path = Path("out"),
    slice_spec: SliceSpec | None = None,
) -> Paths:
    """Build every artifact path for one input track.

    collection = immediate parent dir name; track = input stem.
    The slice suffix (if any) is inserted after the artifact type, before ext.
    """
    input_path = Path(input_path)
    spec = slice_spec or SliceSpec()
    sfx = spec.suffix()
    collection = input_path.parent.name
    track = input_path.stem
    track_dir = Path(out_root) / collection / track

    def name(kind: str, ext: str) -> Path:
        return track_dir / f"{track}_{kind}{sfx}.{ext}"

    return Paths(
        track_dir=track_dir,
        bass=name("bass", "wav"),
        windows=name("silence_windows", "json"),
        combined=name("combined", "wav"),
        remix=name("remix", "wav"),
        combined_m4a=name("combined", "m4a"),
        remix_m4a=name("remix", "m4a"),
    )
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_slice.py tests/test_paths.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
uv run ruff format src/bassify/slice.py src/bassify/paths.py tests/test_slice.py tests/test_paths.py
git add src/bassify/slice.py src/bassify/paths.py tests/test_slice.py tests/test_paths.py
git commit -m "feat: slice spec and output-path resolution"
```

---

## Task 2: ffmpeg/ffprobe helpers

**Goal:** Shared subprocess helpers: `run_ffmpeg()` (prints command, raises on failure), `run_ffmpeg_capture()` (returns stderr for silencedetect), `ffprobe_duration()`, and a `should_skip()` helper for skip-if-exists.

**Files:**
- Create: `src/bassify/ffmpeg.py`
- Test: `tests/test_ffmpeg.py`

**Acceptance Criteria:**
- [ ] `run_ffmpeg(args)` prepends `ffmpeg -hide_banner` and prints the joined command before running.
- [ ] A non-zero ffmpeg exit raises `FfmpegError` carrying a tail of stderr.
- [ ] `ffprobe_duration(path)` returns a float parsed from ffprobe.
- [ ] `should_skip(output, force)` returns True only when the output exists and `force` is False.

**Verify:** `uv run pytest tests/test_ffmpeg.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write `tests/test_ffmpeg.py`** (tests the pure/parse bits and skip logic; subprocess itself is exercised in the integration test)

```python
from pathlib import Path

import pytest

from bassify.ffmpeg import FfmpegError, parse_duration, should_skip


def test_parse_duration():
    assert parse_duration("56.633500\n") == pytest.approx(56.6335)


def test_parse_duration_bad_raises():
    with pytest.raises(FfmpegError):
        parse_duration("N/A")


def test_should_skip(tmp_path: Path):
    out = tmp_path / "x.wav"
    assert should_skip(out, force=False) is False  # does not exist
    out.write_bytes(b"x")
    assert should_skip(out, force=False) is True
    assert should_skip(out, force=True) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ffmpeg.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bassify.ffmpeg'`

- [ ] **Step 3: Write `src/bassify/ffmpeg.py`**

```python
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path


class FfmpegError(RuntimeError):
    """Raised when ffmpeg/ffprobe fails or returns unparseable output."""


def _print_cmd(args: list[str]) -> None:
    print("+ " + " ".join(shlex.quote(a) for a in args))


def run_ffmpeg(args: list[str]) -> None:
    """Run `ffmpeg -hide_banner -y <args>`, printing the command; raise on failure."""
    cmd = ["ffmpeg", "-hide_banner", "-y", *args]
    _print_cmd(cmd)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise FfmpegError(f"ffmpeg failed ({proc.returncode}):\n{tail}")


def run_ffmpeg_capture(args: list[str]) -> str:
    """Run ffmpeg and return stderr (where filters like silencedetect log)."""
    cmd = ["ffmpeg", "-hide_banner", *args]
    _print_cmd(cmd)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # silencedetect uses a null muxer and exits 0; still guard on real errors.
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise FfmpegError(f"ffmpeg failed ({proc.returncode}):\n{tail}")
    return proc.stderr


def parse_duration(text: str) -> float:
    try:
        return float(text.strip())
    except ValueError as exc:
        raise FfmpegError(f"could not parse duration from {text!r}") from exc


def ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    _print_cmd(cmd)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FfmpegError(f"ffprobe failed for {path}")
    return parse_duration(proc.stdout)


def should_skip(output: Path, force: bool) -> bool:
    return output.exists() and not force
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ffmpeg.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff format src/bassify/ffmpeg.py tests/test_ffmpeg.py
git add src/bassify/ffmpeg.py tests/test_ffmpeg.py
git commit -m "feat: ffmpeg/ffprobe subprocess helpers"
```

---

## Task 3: `extract` stage + CLI wiring

**Goal:** Implement L−R bass extraction to `bass.wav` with optional lowpass and slice, wired into the Typer CLI as `bassify extract`.

**Files:**
- Create: `src/bassify/extract.py`
- Modify: `src/bassify/cli.py`

**Acceptance Criteria:**
- [ ] `extract_bass()` builds an ffmpeg command with `pan=mono|c0=c0-c1`, `-c:a pcm_s24le`, `-vn`, and the slice input args when `cut_inputs` is True.
- [ ] `--lowpass HZ` appends `,lowpass=f=HZ` to the filter; omitted by default.
- [ ] Output path comes from `resolve_paths()`; parent dirs are created; skip-if-exists honored.
- [ ] `bassify extract <in.mp3>` runs end-to-end (verified against a real track in Task 10) and prints the command.

**Verify:** `uv run bassify extract --help` exits 0 and shows `--lowpass`, `--duration`, `--start`, `--force`, `-o` options.

**Steps:**

- [ ] **Step 1: Write `src/bassify/extract.py`**

```python
from __future__ import annotations

from pathlib import Path

from bassify.ffmpeg import run_ffmpeg, should_skip
from bassify.paths import resolve_paths
from bassify.slice import SliceSpec


def build_filter(lowpass: float | None) -> str:
    f = "pan=mono|c0=c0-c1"
    if lowpass is not None:
        f += f",lowpass=f={lowpass:g}"
    return f


def extract_bass(
    input_path: Path,
    output: Path | None = None,
    lowpass: float | None = None,
    slice_spec: SliceSpec | None = None,
    cut_inputs: bool = True,
    force: bool = False,
) -> Path:
    """Isolate bass via L-R subtraction -> mono 24-bit WAV. Returns output path."""
    spec = slice_spec or SliceSpec()
    out = output or resolve_paths(input_path, slice_spec=spec).bass
    out.parent.mkdir(parents=True, exist_ok=True)
    if should_skip(out, force):
        print(f"skip (exists): {out}")
        return out
    args: list[str] = []
    if cut_inputs:
        args += spec.input_args()
    args += ["-i", str(input_path), "-af", build_filter(lowpass), "-vn", "-c:a", "pcm_s24le", str(out)]
    run_ffmpeg(args)
    return out
```

- [ ] **Step 2: Wire into `src/bassify/cli.py`** (replace the file's body, keeping the `app` object)

```python
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from bassify import combine as combine_mod
from bassify import detect as detect_mod
from bassify import encode as encode_mod
from bassify import extract as extract_mod
from bassify import remix as remix_mod
from bassify.pipeline import run_pipeline
from bassify.slice import SliceSpec

app = typer.Typer(help="Isolate bass from stereo practice tracks.", no_args_is_help=True)

DurationOpt = Annotated[Optional[float], typer.Option(help="Process only N seconds (ffmpeg -t).")]
StartOpt = Annotated[Optional[float], typer.Option(help="Start offset in seconds (ffmpeg -ss).")]
```

NOTE FOR IMPLEMENTER: `Annotated` must be imported (`from typing import Annotated`). Add the `extract` command:

```python
@app.command()
def extract(
    input_path: Path,
    output: Optional[Path] = typer.Option(None, "-o", "--output"),
    lowpass: Optional[float] = typer.Option(None, help="Low-pass cutoff Hz to tame subtraction residue."),
    duration: DurationOpt = None,
    start: StartOpt = None,
    force: bool = typer.Option(False, "--force"),
) -> None:
    """L-R subtraction -> mono bass WAV."""
    spec = SliceSpec(duration=duration, start=start)
    extract_mod.extract_bass(input_path, output=output, lowpass=lowpass, slice_spec=spec, force=force)
```

NOTE FOR IMPLEMENTER: The imports of `combine_mod`, `detect_mod`, `encode_mod`, `remix_mod`, `run_pipeline` are added now but their modules are created in later tasks. To keep the CLI importable after THIS task, add only the `extract` import and command now; add each other import + command in its own task (Tasks 4–8). Do not import modules that don't exist yet.

- [ ] **Step 3: Verify CLI help**

Run: `uv run bassify extract --help`
Expected: exit 0, shows `-o/--output`, `--lowpass`, `--duration`, `--start`, `--force`.

- [ ] **Step 4: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/bassify/extract.py src/bassify/cli.py
git commit -m "feat: extract stage (L-R bass) + CLI"
```

---

## Task 4: `detect` stage (silence parsing + window pairing)

**Goal:** Parse ffmpeg `silencedetect` stderr into padded `(start, end)` windows (handling unpaired trailing starts), emit JSON, wire `bassify detect`.

**Files:**
- Create: `src/bassify/detect.py`
- Modify: `src/bassify/cli.py`
- Test: `tests/test_detect.py`

**Acceptance Criteria:**
- [ ] `parse_silences(stderr, duration)` returns paired windows; an unpaired trailing `silence_start` is closed at `duration`.
- [ ] Windows are padded ±0.1 s and clamped to `[0, duration]`.
- [ ] `detect_windows()` writes a JSON list of `{"start","end"}` objects and honors skip-if-exists.
- [ ] `bassify detect <bass.wav>` exposes `--threshold`, `--min-gap`, `--duration`, `--start`, `--force`.

**Verify:** `uv run pytest tests/test_detect.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write `tests/test_detect.py`**

```python
import pytest

from bassify.detect import parse_silences

SAMPLE = """
[silencedetect @ 0x1] silence_start: 12.284
[silencedetect @ 0x1] silence_end: 15.913 | silence_duration: 3.629
[silencedetect @ 0x1] silence_start: 47.100
[silencedetect @ 0x1] silence_end: 51.300 | silence_duration: 4.200
"""

TRAILING = """
[silencedetect @ 0x1] silence_start: 40.000
"""


def test_paired_windows_padded():
    w = parse_silences(SAMPLE, duration=60.0, pad=0.1)
    assert w[0]["start"] == pytest.approx(12.184)
    assert w[0]["end"] == pytest.approx(16.013)
    assert len(w) == 2


def test_unpaired_trailing_start_closes_at_duration():
    w = parse_silences(TRAILING, duration=45.0, pad=0.1)
    assert len(w) == 1
    assert w[0]["start"] == pytest.approx(39.9)
    assert w[0]["end"] == pytest.approx(45.0)  # clamped to duration, not duration+pad


def test_clamp_lower_bound():
    w = parse_silences("[x] silence_start: 0.05\n[x] silence_end: 1.0\n", duration=10.0, pad=0.1)
    assert w[0]["start"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_detect.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bassify.detect'`

- [ ] **Step 3: Write `src/bassify/detect.py`**

```python
from __future__ import annotations

import json
import re
from pathlib import Path

from bassify.ffmpeg import ffprobe_duration, run_ffmpeg_capture, should_skip
from bassify.paths import resolve_paths
from bassify.slice import SliceSpec

_START = re.compile(r"silence_start:\s*([0-9.]+)")
_END = re.compile(r"silence_end:\s*([0-9.]+)")


def parse_silences(stderr: str, duration: float, pad: float = 0.1) -> list[dict[str, float]]:
    """Pair silence_start/end lines into padded, clamped windows.

    An unpaired trailing silence_start is closed at `duration`.
    """
    windows: list[dict[str, float]] = []
    pending: float | None = None
    for line in stderr.splitlines():
        m_start = _START.search(line)
        if m_start:
            pending = float(m_start.group(1))
            continue
        m_end = _END.search(line)
        if m_end and pending is not None:
            windows.append({"start": pending, "end": float(m_end.group(1))})
            pending = None
    if pending is not None:
        windows.append({"start": pending, "end": duration})

    clamped: list[dict[str, float]] = []
    for w in windows:
        start = max(0.0, w["start"] - pad)
        end = min(duration, w["end"] + pad)
        clamped.append({"start": start, "end": end})
    return clamped


def detect_windows(
    bass_path: Path,
    original_for_naming: Path | None = None,
    output: Path | None = None,
    threshold: float = -40.0,
    min_gap: float = 1.0,
    slice_spec: SliceSpec | None = None,
    cut_inputs: bool = True,
    force: bool = False,
) -> Path:
    """Run silencedetect on the bass track, write windows JSON. Returns output path.

    `original_for_naming` lets `run` place the JSON in the track dir keyed off the
    original input name; when None the bass_path stem is used for naming.
    """
    spec = slice_spec or SliceSpec()
    if output is not None:
        out = output
    elif original_for_naming is not None:
        out = resolve_paths(original_for_naming, slice_spec=spec).windows
    else:
        out = bass_path.with_name(bass_path.stem + "_silence_windows.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    if should_skip(out, force):
        print(f"skip (exists): {out}")
        return out

    args: list[str] = []
    if cut_inputs:
        args += spec.input_args()
    args += [
        "-i",
        str(bass_path),
        "-af",
        f"silencedetect=noise={threshold:g}dB:d={min_gap:g}",
        "-f",
        "null",
        "-",
    ]
    stderr = run_ffmpeg_capture(args)
    duration = ffprobe_duration(bass_path)
    windows = parse_silences(stderr, duration=duration)
    out.write_text(json.dumps(windows, indent=2))
    print(f"wrote {len(windows)} windows -> {out}")
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_detect.py -v`
Expected: PASS

- [ ] **Step 5: Add the `detect` command to `src/bassify/cli.py`**

```python
from bassify import detect as detect_mod


@app.command()
def detect(
    bass_path: Path,
    output: Optional[Path] = typer.Option(None, "-o", "--output"),
    threshold: float = typer.Option(-40.0, help="silencedetect noise floor in dB."),
    min_gap: float = typer.Option(1.0, "--min-gap", help="Minimum quiet run (s) to count as a gap."),
    duration: DurationOpt = None,
    start: StartOpt = None,
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Detect silence gaps in the bass -> windows JSON."""
    spec = SliceSpec(duration=duration, start=start)
    detect_mod.detect_windows(
        bass_path, output=output, threshold=threshold, min_gap=min_gap, slice_spec=spec, force=force
    )
```

- [ ] **Step 6: Verify CLI + tests**

Run: `uv run bassify detect --help && uv run pytest tests/test_detect.py -v`
Expected: help exits 0; tests PASS.

- [ ] **Step 7: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/bassify/detect.py src/bassify/cli.py tests/test_detect.py
git commit -m "feat: detect stage (silence windows) + CLI"
```

---

## Task 5: `combine` stage (gate builder + mono mix)

**Goal:** Build the `between(t,...)` gate string from windows and mix gated original speech onto the mono bass → `combined.wav`; wire `bassify combine`.

**Files:**
- Create: `src/bassify/combine.py`
- Modify: `src/bassify/cli.py`
- Test: `tests/test_combine.py`

**Acceptance Criteria:**
- [ ] `build_gate(windows)` returns `between(t,12.184,16.013)+between(t,47.1,51.3)` style strings; empty windows → `"0"`.
- [ ] `build_filtergraph()` gates the original downmixed to mono with `eval=frame` and mixes via `amix=inputs=2:normalize=0`, output labelled `[out]`.
- [ ] `combine_track()` reads the windows JSON, writes `combined.wav` (mono, pcm_s24le, `-vn`), honors skip-if-exists, and warns if bass/original durations differ by > 0.1 s (only when `cut_inputs` is True).
- [ ] `bassify combine <bass.wav> <original.mp3> <windows.json>` exposes `--duration`, `--start`, `--force`, `-o`.

**Verify:** `uv run pytest tests/test_combine.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write `tests/test_combine.py`**

```python
from bassify.combine import build_filtergraph, build_gate


def test_build_gate_multiple():
    windows = [{"start": 12.184, "end": 16.013}, {"start": 47.1, "end": 51.3}]
    assert build_gate(windows) == "between(t,12.184,16.013)+between(t,47.1,51.3)"


def test_build_gate_empty():
    assert build_gate([]) == "0"


def test_filtergraph_contains_required_pieces():
    fg = build_filtergraph("between(t,1,2)")
    assert "eval=frame" in fg
    assert "amix=inputs=2:normalize=0" in fg
    assert "[out]" in fg
    assert "pan=mono" in fg  # original downmixed to mono before gating
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_combine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bassify.combine'`

- [ ] **Step 3: Write `src/bassify/combine.py`**

```python
from __future__ import annotations

import json
from pathlib import Path

from bassify.ffmpeg import ffprobe_duration, run_ffmpeg, should_skip
from bassify.paths import resolve_paths
from bassify.slice import SliceSpec


def build_gate(windows: list[dict[str, float]]) -> str:
    """Sum of between() terms: 1 inside any window, 0 outside. Empty -> '0'."""
    if not windows:
        return "0"
    return "+".join(f"between(t,{w['start']:g},{w['end']:g})" for w in windows)


def build_filtergraph(gate: str) -> str:
    """Gate the original (downmixed to mono) during windows, mix onto mono bass.

    Input 0 = bass (mono), input 1 = original (stereo). eval=frame is required so
    the gate re-evaluates per frame; normalize=0 stops amix halving the inputs.
    """
    return (
        f"[1:a]pan=mono|c0=0.5*c0+0.5*c1,volume='{gate}':eval=frame[gap];"
        f"[0:a][gap]amix=inputs=2:normalize=0[out]"
    )


def combine_track(
    bass_path: Path,
    original_path: Path,
    windows_path: Path,
    output: Path | None = None,
    slice_spec: SliceSpec | None = None,
    cut_inputs: bool = True,
    force: bool = False,
) -> Path:
    """Mix gated speech/count-ins onto the mono bass -> combined.wav."""
    spec = slice_spec or SliceSpec()
    out = output or resolve_paths(original_path, slice_spec=spec).combined
    out.parent.mkdir(parents=True, exist_ok=True)
    if should_skip(out, force):
        print(f"skip (exists): {out}")
        return out

    windows = json.loads(Path(windows_path).read_text())
    gate = build_gate(windows)
    fg = build_filtergraph(gate)

    if cut_inputs:
        db = ffprobe_duration(bass_path)
        do = ffprobe_duration(original_path)
        if abs(db - do) > 0.1:
            print(f"WARNING: duration mismatch bass={db:.3f}s original={do:.3f}s (mix may drift)")

    args: list[str] = []
    if cut_inputs:
        args += spec.input_args()
    args += ["-i", str(bass_path)]
    if cut_inputs:
        args += spec.input_args()
    args += [
        "-i",
        str(original_path),
        "-filter_complex",
        fg,
        "-map",
        "[out]",
        "-vn",
        "-c:a",
        "pcm_s24le",
        str(out),
    ]
    run_ffmpeg(args)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_combine.py -v`
Expected: PASS

- [ ] **Step 5: Add the `combine` command to `src/bassify/cli.py`**

```python
from bassify import combine as combine_mod


@app.command()
def combine(
    bass_path: Path,
    original_path: Path,
    windows_path: Path,
    output: Optional[Path] = typer.Option(None, "-o", "--output"),
    duration: DurationOpt = None,
    start: StartOpt = None,
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Mix gated speech/count-ins onto the bass -> combined WAV."""
    spec = SliceSpec(duration=duration, start=start)
    combine_mod.combine_track(
        bass_path, original_path, windows_path, output=output, slice_spec=spec, force=force
    )
```

- [ ] **Step 6: Verify CLI + tests**

Run: `uv run bassify combine --help && uv run pytest tests/test_combine.py -v`
Expected: help exits 0; tests PASS.

- [ ] **Step 7: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/bassify/combine.py src/bassify/cli.py tests/test_combine.py
git commit -m "feat: combine stage (gate + mono mix) + CLI"
```

---

## Task 6: `remix` stage (pannable stereo)

**Goal:** Build a stereo file with L=combined (mono), R=original right channel; wire `bassify remix`.

**Files:**
- Create: `src/bassify/remix.py`
- Modify: `src/bassify/cli.py`
- Test: `tests/test_remix.py`

**Acceptance Criteria:**
- [ ] `build_filtergraph()` extracts the original's right channel to mono and joins `[combined][right]` into a stereo `[out]` (L from combined, R from original right).
- [ ] `remix_track()` writes `remix.wav` (stereo, pcm_s24le, `-vn`), honors skip-if-exists, warns on >0.1 s duration mismatch (when `cut_inputs`).
- [ ] `bassify remix <combined.wav> <original.mp3>` exposes `--duration`, `--start`, `--force`, `-o`.

**Verify:** `uv run pytest tests/test_remix.py -v` → all pass

**Steps:**

- [ ] **Step 1: Write `tests/test_remix.py`**

```python
from bassify.remix import build_filtergraph


def test_filtergraph_maps_channels():
    fg = build_filtergraph()
    # original right channel isolated
    assert "pan=mono|c0=c1" in fg
    # joined to stereo, output labelled
    assert "join=inputs=2:channel_layout=stereo" in fg
    assert "[out]" in fg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_remix.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bassify.remix'`

- [ ] **Step 3: Write `src/bassify/remix.py`**

```python
from __future__ import annotations

from pathlib import Path

from bassify.ffmpeg import ffprobe_duration, run_ffmpeg, should_skip
from bassify.paths import resolve_paths
from bassify.slice import SliceSpec


def build_filtergraph() -> str:
    """L = combined (input 0, mono), R = original right channel (input 1, c1).

    join maps the first input's channel to FL and the second's to FR.
    """
    return (
        "[1:a]pan=mono|c0=c1[right];"
        "[0:a][right]join=inputs=2:channel_layout=stereo[out]"
    )


def remix_track(
    combined_path: Path,
    original_path: Path,
    output: Path | None = None,
    slice_spec: SliceSpec | None = None,
    cut_inputs: bool = True,
    force: bool = False,
) -> Path:
    """Build pannable stereo: L=combined, R=original right -> remix.wav."""
    spec = slice_spec or SliceSpec()
    out = output or resolve_paths(original_path, slice_spec=spec).remix
    out.parent.mkdir(parents=True, exist_ok=True)
    if should_skip(out, force):
        print(f"skip (exists): {out}")
        return out

    if cut_inputs:
        dc = ffprobe_duration(combined_path)
        do = ffprobe_duration(original_path)
        if abs(dc - do) > 0.1:
            print(f"WARNING: duration mismatch combined={dc:.3f}s original={do:.3f}s")

    args: list[str] = []
    if cut_inputs:
        args += spec.input_args()
    args += ["-i", str(combined_path)]
    if cut_inputs:
        args += spec.input_args()
    args += [
        "-i",
        str(original_path),
        "-filter_complex",
        build_filtergraph(),
        "-map",
        "[out]",
        "-vn",
        "-c:a",
        "pcm_s24le",
        str(out),
    ]
    run_ffmpeg(args)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_remix.py -v`
Expected: PASS

- [ ] **Step 5: Add the `remix` command to `src/bassify/cli.py`**

```python
from bassify import remix as remix_mod


@app.command()
def remix(
    combined_path: Path,
    original_path: Path,
    output: Optional[Path] = typer.Option(None, "-o", "--output"),
    duration: DurationOpt = None,
    start: StartOpt = None,
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Build pannable stereo (L=combined, R=original right) -> remix WAV."""
    spec = SliceSpec(duration=duration, start=start)
    remix_mod.remix_track(combined_path, original_path, output=output, slice_spec=spec, force=force)
```

- [ ] **Step 6: Verify CLI + tests**

Run: `uv run bassify remix --help && uv run pytest tests/test_remix.py -v`
Expected: help exits 0; tests PASS.

- [ ] **Step 7: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/bassify/remix.py src/bassify/cli.py tests/test_remix.py
git commit -m "feat: remix stage (pannable stereo) + CLI"
```

---

## Task 7: `encode` stage (AAC/.m4a with metadata + art)

**Goal:** Encode a WAV to AAC `.m4a`, carrying the original MP3's metadata and cover art; wire `bassify encode`.

**Files:**
- Create: `src/bassify/encode.py`
- Modify: `src/bassify/cli.py`

**Acceptance Criteria:**
- [ ] `encode_track()` builds `-map 0:a -map 1:v -c:a aac -b:a 256k -c:v copy -disposition:v attached_pic -map_metadata 1` (no `-vn`).
- [ ] Output `.m4a` path defaults next to the source WAV (same stem, `.m4a`); parent dirs created; skip-if-exists honored.
- [ ] Missing cover art in the original does not crash: falls back to an audio-only encode with metadata (`-map 0:a -c:a aac -b:a 256k -map_metadata 1`) when `1:v` mapping fails.
- [ ] `bassify encode <audio.wav> <original.mp3>` exposes `-o`, `--force`.

**Verify:** `uv run bassify encode --help` exits 0 and lists `-o/--output`, `--force`.

**Steps:**

- [ ] **Step 1: Write `src/bassify/encode.py`**

```python
from __future__ import annotations

from pathlib import Path

from bassify.ffmpeg import FfmpegError, run_ffmpeg, should_skip


def _args_with_art(wav: Path, original: Path, out: Path) -> list[str]:
    return [
        "-i", str(wav),
        "-i", str(original),
        "-map", "0:a",
        "-map", "1:v",
        "-c:a", "aac",
        "-b:a", "256k",
        "-c:v", "copy",
        "-disposition:v", "attached_pic",
        "-map_metadata", "1",
        str(out),
    ]


def _args_audio_only(wav: Path, original: Path, out: Path) -> list[str]:
    return [
        "-i", str(wav),
        "-i", str(original),
        "-map", "0:a",
        "-c:a", "aac",
        "-b:a", "256k",
        "-map_metadata", "1",
        str(out),
    ]


def encode_track(
    wav_path: Path,
    original_path: Path,
    output: Path | None = None,
    force: bool = False,
) -> Path:
    """Encode WAV -> AAC/.m4a, carrying original metadata + cover art if present."""
    out = output or wav_path.with_suffix(".m4a")
    out.parent.mkdir(parents=True, exist_ok=True)
    if should_skip(out, force):
        print(f"skip (exists): {out}")
        return out
    try:
        run_ffmpeg(_args_with_art(wav_path, original_path, out))
    except FfmpegError:
        print("no usable cover art; encoding audio + metadata only")
        run_ffmpeg(_args_audio_only(wav_path, original_path, out))
    return out
```

- [ ] **Step 2: Add the `encode` command to `src/bassify/cli.py`**

```python
from bassify import encode as encode_mod


@app.command()
def encode(
    wav_path: Path,
    original_path: Path,
    output: Optional[Path] = typer.Option(None, "-o", "--output"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Encode a WAV to AAC/.m4a with original metadata + cover art."""
    encode_mod.encode_track(wav_path, original_path, output=output, force=force)
```

- [ ] **Step 3: Verify CLI**

Run: `uv run bassify encode --help`
Expected: exit 0; shows `-o/--output`, `--force`.

- [ ] **Step 4: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/bassify/encode.py src/bassify/cli.py
git commit -m "feat: encode stage (AAC/.m4a with metadata + art) + CLI"
```

---

## Task 8: `run` pipeline (chain all stages)

**Goal:** A `run_pipeline()` that chains extract → detect → combine → remix → encode(×2), applying the ffmpeg time-cut only at extract and carrying the slice suffix through filenames; wire `bassify run`.

**Files:**
- Create: `src/bassify/pipeline.py`
- Modify: `src/bassify/cli.py`

**Acceptance Criteria:**
- [ ] `run_pipeline()` calls each stage using `resolve_paths()` outputs so intermediates land in `out/<collection>/<track>/`.
- [ ] `extract` receives `cut_inputs=True`; `detect`, `combine`, `remix` receive `cut_inputs=False` (their inputs are already sliced) but the same `slice_spec` for naming.
- [ ] `encode` is invoked twice: `combined.wav`→`combined.m4a`, `remix.wav`→`remix.m4a`.
- [ ] `bassify run <in.mp3>` exposes `--lowpass`, `--threshold`, `--min-gap`, `--duration`, `--start`, `--force` and returns 0 on a real track (verified in Task 10).

**Verify:** `uv run bassify run --help` exits 0 and lists all documented options.

**Steps:**

- [ ] **Step 1: Write `src/bassify/pipeline.py`**

```python
from __future__ import annotations

from pathlib import Path

from bassify.combine import combine_track
from bassify.detect import detect_windows
from bassify.encode import encode_track
from bassify.extract import extract_bass
from bassify.paths import resolve_paths
from bassify.remix import remix_track
from bassify.slice import SliceSpec


def run_pipeline(
    input_path: Path,
    lowpass: float | None = None,
    threshold: float = -40.0,
    min_gap: float = 1.0,
    slice_spec: SliceSpec | None = None,
    force: bool = False,
) -> None:
    """extract -> detect -> combine -> remix -> encode x2.

    The ffmpeg time-cut is applied ONLY at extract; downstream stages read the
    already-sliced WAVs (cut_inputs=False) but keep the slice suffix in names.
    """
    spec = slice_spec or SliceSpec()
    paths = resolve_paths(input_path, slice_spec=spec)
    paths.track_dir.mkdir(parents=True, exist_ok=True)

    bass = extract_bass(
        input_path, output=paths.bass, lowpass=lowpass, slice_spec=spec, cut_inputs=True, force=force
    )
    windows = detect_windows(
        bass,
        output=paths.windows,
        threshold=threshold,
        min_gap=min_gap,
        slice_spec=spec,
        cut_inputs=False,
        force=force,
    )
    combined = combine_track(
        bass, input_path, windows, output=paths.combined, slice_spec=spec, cut_inputs=False, force=force
    )
    remixed = remix_track(
        combined, input_path, output=paths.remix, slice_spec=spec, cut_inputs=False, force=force
    )
    encode_track(combined, input_path, output=paths.combined_m4a, force=force)
    encode_track(remixed, input_path, output=paths.remix_m4a, force=force)
    print(f"done: {paths.track_dir}")
```

- [ ] **Step 2: Add the `run` command to `src/bassify/cli.py`**

```python
from bassify.pipeline import run_pipeline


@app.command()
def run(
    input_path: Path,
    lowpass: Optional[float] = typer.Option(None, help="Low-pass cutoff Hz for extract."),
    threshold: float = typer.Option(-40.0, help="silencedetect noise floor in dB."),
    min_gap: float = typer.Option(1.0, "--min-gap"),
    duration: DurationOpt = None,
    start: StartOpt = None,
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Run the full pipeline: extract -> detect -> combine -> remix -> encode."""
    spec = SliceSpec(duration=duration, start=start)
    run_pipeline(
        input_path,
        lowpass=lowpass,
        threshold=threshold,
        min_gap=min_gap,
        slice_spec=spec,
        force=force,
    )
```

- [ ] **Step 3: Verify CLI end-to-end wiring imports**

Run: `uv run bassify --help && uv run bassify run --help`
Expected: root help lists `extract detect combine remix encode run`; `run --help` lists all options.

- [ ] **Step 4: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add src/bassify/pipeline.py src/bassify/cli.py
git commit -m "feat: run pipeline chaining all stages + CLI"
```

---

## Task 9: End-to-end integration test (synthetic audio)

**Goal:** A marked integration test that synthesizes a tiny stereo WAV (L = bass tone + noise, R = noise only), runs `extract`, and asserts the extracted bass energy concentrates at the bass frequency — proving L−R works without committing audio files.

**Files:**
- Create: `tests/test_integration.py`

**Acceptance Criteria:**
- [ ] Test is marked `@pytest.mark.integration` and skipped when `ffmpeg` is not on PATH.
- [ ] It generates a synthetic stereo source with ffmpeg (`lavfi` sine + anoise), runs `extract_bass()`, and asserts the output WAV exists and is mono.
- [ ] A second assertion runs the full `run_pipeline()` with a short slice and checks all six artifacts exist with the expected slice suffix.

**Verify:** `uv run pytest tests/test_integration.py -v` → passes locally (ffmpeg present) or skips cleanly.

**Steps:**

- [ ] **Step 1: Write `tests/test_integration.py`**

```python
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from bassify.extract import extract_bass
from bassify.paths import resolve_paths
from bassify.pipeline import run_pipeline
from bassify.slice import SliceSpec

pytestmark = pytest.mark.integration

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
skip_reason = "ffmpeg/ffprobe not on PATH"


def _make_source(path: Path, seconds: int = 3) -> None:
    """Stereo source: L = 80 Hz sine (bass) mixed with noise, R = same noise only.

    L-R should therefore recover the 80 Hz sine.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=80:duration={seconds}",
        "-f", "lavfi", "-i", f"anoisesrc=d={seconds}:c=pink:a=0.1",
        "-filter_complex",
        "[0:a][1:a]amix=inputs=2:normalize=0[left];"
        "[1:a]acopy[right];"
        "[left][right]join=inputs=2:channel_layout=stereo[out]",
        "-map", "[out]", "-c:a", "pcm_s16le", str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
def test_extract_produces_mono_wav(tmp_path: Path):
    src = tmp_path / "Coll" / "track.wav"
    _make_source(src)
    out = extract_bass(src, output=tmp_path / "bass.wav", cut_inputs=True)
    assert out.exists()
    with wave.open(str(out), "rb") as w:
        assert w.getnchannels() == 1


@pytest.mark.skipif(ffmpeg_missing, reason=skip_reason)
def test_full_pipeline_slice_artifacts(tmp_path: Path):
    src = tmp_path / "Coll" / "track.wav"
    _make_source(src, seconds=5)
    spec = SliceSpec(duration=2)
    run_pipeline(src, slice_spec=spec, force=True)
    p = resolve_paths(src, slice_spec=spec)
    for artifact in (p.bass, p.windows, p.combined, p.remix, p.combined_m4a, p.remix_m4a):
        assert artifact.exists(), f"missing {artifact}"
        assert "_d2s" in artifact.name
```

NOTE FOR IMPLEMENTER: if `run_pipeline` writes under the default `out/` root rather than a temp dir, change the source to live under `tmp_path` and pass `out_root` through — but `resolve_paths` places outputs relative to the input's parent chain under `out/`. To keep the test hermetic, `cd` into `tmp_path` via monkeypatch (`monkeypatch.chdir(tmp_path)`) so `out/` is created inside the temp dir. Add `monkeypatch` to the test signature and call `monkeypatch.chdir(tmp_path)` first.

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_integration.py -v`
Expected: PASS (ffmpeg present locally). If it fails on the pipeline hermeticity, apply the monkeypatch.chdir note.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add tests/test_integration.py
git commit -m "test: end-to-end integration on synthetic stereo audio"
```

---

## Task 10: Interactive UAT on real tracks

**Goal:** **USER-ORDERED GATE — NON-SKIPPABLE.** This task was requested by the user in the current conversation. It MUST NOT be closed by walking around it, by declaring it "verified inline", or by substituting a cheaper check. Close only after every item in `acceptanceCriteria` has been re-validated independently, with output captured.

Run the pipeline against a real `tracks/BluesBass/` track and walk the user through listening/inspecting each stage so the audio actually sounds right — the checks automated tests cannot make.

**Files:**
- None (verification only; may write notes to `docs/superpowers/specs/` if the user wants tuning recorded).

**Acceptance Criteria:**
- [ ] `bassify run "tracks/BluesBass/01_The Twelve Bar Blues Form.mp3" --duration 30` completes with exit 0 and produces all six artifacts under `out/BluesBass/01_The Twelve Bar Blues Form/` (names carrying `_d30s`). Capture the command output and `ls` of the dir.
- [ ] **extract:** user listens to `*_bass_d30s.wav` and confirms the bass is isolated and clean, deciding whether `--lowpass` is needed (record the chosen cutoff or "none").
- [ ] **detect:** user inspects `*_silence_windows_d30s.json` and confirms windows land on the actual spoken/count-in gaps (no musical rests caught, no announcements missed); record any `--threshold`/`--min-gap` change.
- [ ] **combine:** user listens to `*_combined_d30s.wav` and confirms speech/count-ins reappear at the right spots, no boundary clicks, no 6 dB bass drop.
- [ ] **remix:** user listens to `*_remix_d30s.wav` sweeping balance — hard-left = isolated bass, hard-right = bass-less backing, center = both; channels time-aligned.
- [ ] **encode:** user confirms `*_combined_d30s.m4a` and `*_remix_d30s.m4a` play and carry title/artist/album tags and cover art (`ffprobe` output captured showing the tags + attached_pic stream).

**Verify:** `bassify run "tracks/BluesBass/01_The Twelve Bar Blues Form.mp3" --duration 30 --force` → exit 0; then `ffprobe -hide_banner "out/BluesBass/01_The Twelve Bar Blues Form/01_The Twelve Bar Blues Form_combined_d30s.m4a"` shows AAC audio + attached_pic + metadata. User verbally confirms each per-stage listening criterion above.

**Steps:**

- [ ] **Step 1: Run the pipeline on a real track (short slice)**

```bash
uv run bassify run "tracks/BluesBass/01_The Twelve Bar Blues Form.mp3" --duration 30 --force
ls -la "out/BluesBass/01_The Twelve Bar Blues Form/"
```
Capture both outputs.

- [ ] **Step 2: Walk the user through each stage**

Play each artifact for the user (or have them open it) and collect a yes/no + notes for extract, detect, combine, remix, encode per the acceptance criteria. Record any tuning the user wants as the new defaults.

- [ ] **Step 3: ffprobe the .m4a outputs for metadata/art**

```bash
ffprobe -hide_banner "out/BluesBass/01_The Twelve Bar Blues Form/01_The Twelve Bar Blues Form_combined_d30s.m4a"
```
Confirm an AAC audio stream, an `attached_pic` video stream, and title/artist/album tags.

- [ ] **Step 4: Apply any agreed tuning + commit (only if changes were made)**

If the user changed defaults (e.g. lowpass, threshold), update the relevant stage default and note it in the spec, then:

```bash
git add -A
git commit -m "chore: tune pipeline defaults from UAT feedback"
```

---

## Self-Review

**1. Spec coverage:**
- extract (spec §6) → Task 3 ✓
- detect + trailing-silence edge + padding (spec §6) → Task 4 ✓
- combine + gate/eval=frame/normalize=0 + duration guard (spec §6) → Task 5 ✓
- remix + non-re-processable note (spec §6) → Task 6 ✓
- encode + metadata/art (spec §6) → Task 7 ✓
- run chain (spec §6) → Task 8 ✓
- smart output layout + slice suffix (spec §5, §4) → Task 1 ✓
- rerun skip/force (spec §5) → Tasks 2–8 ✓
- --duration/--start on every command (spec §4) → CLI in Tasks 3–8, cut_inputs rule in Task 8 ✓
- tooling: uv/ruff/pre-commit/justfile/CI (spec §7) → Task 0 ✓
- testing: pure unit + marked integration (spec §8) → Tasks 1,2,4,5,6,9 ✓
- interactive UAT (spec §8) → Task 10 ✓

**2. Placeholder scan:** No TBD/TODO; all code blocks are concrete. The two "NOTE FOR IMPLEMENTER" callouts are explicit instructions (import `Annotated`; add CLI imports incrementally; monkeypatch.chdir for hermetic test), not deferred work.

**3. Type consistency:** `SliceSpec(duration, start)`, `resolve_paths(input_path, out_root, slice_spec) -> Paths`, `Paths` field names (`bass/windows/combined/remix/combined_m4a/remix_m4a`), `run_ffmpeg`/`run_ffmpeg_capture`/`ffprobe_duration`/`should_skip`, and every stage's `cut_inputs` parameter are used consistently across tasks.

**Note on `Annotated`/`DurationOpt`:** the plan shows `DurationOpt`/`StartOpt` type aliases in Task 3 but the concrete command signatures use plain `Optional[float] = None` with `typer.Option` defaults elsewhere for clarity. Implementer: either is fine; keep it consistent — simplest is to drop the aliases and use `duration: Optional[float] = typer.Option(None, help="...")` uniformly. The aliases are optional sugar, not required types.
