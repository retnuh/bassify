# Bassify — Audio Pipeline Design (V1)

Design spec for the first working version of `bassify`, a small CLI that isolates
the bass line from practice tracks recorded in a specific stereo format and
recombines spoken announcements / count-ins onto the bass.

Source brief: [`docs/bass-extraction-pipeline.md`](../../bass-extraction-pipeline.md).
This spec covers **V1 = audio only** (`extract`, `detect`, `combine`). The video
`render` stage (CQT + waveform) is deferred to a later phase.

---

## 1. Scope

**In scope (V1):**
- `extract` — isolate bass via L−R channel subtraction.
- `detect` — find silence gaps in the bass, emit JSON windows.
- `combine` — gate the original mix during those windows, mix onto the bass.
- `run` — chain the three stages, leaving inspectable intermediates on disk.
- Smart, reusable output-path layout mirroring the input collection.
- Modern Python tooling: uv, ruff, pre-commit, justfile, GitHub CI.

**Out of scope (deferred):**
- `render` — CQT/waveform video (brief §4). Later phase.
- numpy/soundfile in-process DSP. V1 shells out to ffmpeg (brief says this
  suffices for all stages).
- Silero VAD speech labelling (brief §2 optional).

## 2. Source format assumption

Practice tracks are stereo where **left = full mix**, **right = full mix minus
bass**. Subtracting R from L leaves the bass. Verified on the real set
(`tracks/BluesBass/`, 43 MP3s, 256 kbps 44.1 kHz stereo): left channel measured
~5 dB hotter than right, consistent with the format.

**Caveat carried from the brief:** MP3 joint stereo does not encode L/R
independently, so subtraction leaves a faint high-frequency ghost. Acceptable for
learning a bass line; the optional `--lowpass` mitigates if distracting. Test the
plain (unfiltered) version first.

**Gotcha:** these MP3s carry embedded cover art (an mjpeg stream). Every ffmpeg
invocation must strip video (`-vn`) or map audio only, or the artwork leaks into
outputs.

## 3. Architecture

Each stage is a function that builds an ffmpeg command, runs it via
`subprocess`, and returns paths/data. Stages are independently runnable and their
artifacts are inspectable on disk. Data flow:

```
input.mp3  ──extract──▶  <track>_bass.wav
<track>_bass.wav  ──detect──▶  <track>_silence_windows.json
<track>_bass.wav + input.mp3 + windows.json  ──combine──▶  <track>_combined.wav
```

Each arrow is one stage; each artifact is inspectable and hand-correctable before
the next stage consumes it.

### Package layout (`src/` layout)

```
bassify/
  pyproject.toml            # uv-managed; metadata, deps, ruff config
  justfile                  # task runner
  .pre-commit-config.yaml   # ruff lint + format hooks
  .github/workflows/ci.yml
  src/bassify/
    __init__.py
    cli.py                  # Typer app + subcommands
    paths.py                # resolve_paths(): collection/track/artifact paths
    ffmpeg.py               # run_ffmpeg(), ffprobe_duration()
    extract.py
    detect.py               # silencedetect stderr parse, window pairing, JSON
    combine.py              # gate-string builder, mix
  tests/
  docs/                     # existing brief + this spec
  tracks/BluesBass/         # symlink to external source (gitignored)
  out/                      # generated artifacts (gitignored)
```

### Shared helpers (`ffmpeg.py`)

- `run_ffmpeg(args)` — prints the full command before running (debuggability),
  runs subprocess, raises on nonzero exit with a tail of stderr.
- `ffprobe_duration(path) -> float` — used for trailing-silence handling and the
  length guard in `combine`.

## 4. CLI (Typer)

```
bassify extract  <in.mp3>  [-o PATH] [--lowpass HZ] [--force]
bassify detect   <bass.wav> [-o PATH] [--threshold -40] [--min-gap 1.0] [--force]
bassify combine  <bass.wav> <original.mp3> <windows.json> [-o PATH] [--force]
bassify run      <in.mp3>  [--lowpass HZ] [--threshold -40] [--min-gap 1.0] [--force]
```

- Typer chosen over argparse: subcommands map to typed functions, clean `--help`,
  autocompletion, standard in the modern uv/ruff ecosystem.
- `run` chains extract → detect → combine, writing every intermediate to the
  track's output dir so windows can be hand-corrected between runs.
- `-o` overrides a single artifact path for one-off use; default is the smart
  layout below.
- Every ffmpeg command is printed before execution.

## 5. Output-path layout

Path resolution is centralized in one `resolve_paths(input_path, out_root="out")`
helper used by every stage — no ad-hoc path building.

- **Collection dir** = the immediate parent directory name of the input file.
- **Per-track subdir** = the track basename (extension stripped).
- **Artifact names** = `<track>_bass.wav`, `<track>_silence_windows.json`,
  `<track>_combined.wav`.

Example — input `tracks/BluesBass/01_The Twelve Bar Blues Form.mp3`:

```
out/BluesBass/01_The Twelve Bar Blues Form/
  01_The Twelve Bar Blues Form_bass.wav
  01_The Twelve Bar Blues Form_silence_windows.json
  01_The Twelve Bar Blues Form_combined.wav
```

`out/` is gitignored.

### Rerun behavior

Skip-if-exists by default: a stage whose output already exists skips its work.
`--force` regenerates. This keeps reruns over a 43-track album fast and guards
against accidental clobber.

## 6. Stage internals

### `extract` (brief §1)

- ffmpeg `pan=mono|c0=c0-c1` → mono bass, written as 24-bit PCM
  (`-c:a pcm_s24le`).
- `--lowpass HZ` (optional, default off) appends `,lowpass=f=HZ`. Try 500–1000 Hz
  only if the subtraction residue is distracting.
- `-vn` strips embedded cover art.
- Output: `<track>_bass.wav`.

### `detect` (brief §2)

- ffmpeg `silencedetect=noise=<THRESH>dB:d=<MIN_GAP>` on the bass; capture
  **stderr**.
- Parse `silence_start` / `silence_end` pairs by regex.
- **Trailing-silence edge case:** an unpaired final `silence_start` (track fades
  out) → window end = track duration via `ffprobe_duration`. A missing end never
  breaks the pairing logic.
- Pad each window ±100 ms, clamped to `[0, duration]`.
- Emit JSON: `[{"start": 12.28, "end": 15.91}, ...]`.
- Defaults: `--threshold -40` (bass notes ring; too high a threshold gates a note
  tail mid-decay), `--min-gap 1.0` (keeps genuine musical rests from qualifying as
  gaps).
- Output: `<track>_silence_windows.json`. Hand-correctable before `combine`.

### `combine` (brief §3)

- Read windows JSON → build a gate string
  `between(t,a,b)+between(t,c,d)+...` (sums to 1 inside a window, 0 outside).
- ffmpeg `filter_complex`: gate the original mix with `eval=frame` (required —
  re-evaluates per frame), then `amix=inputs=2:normalize=0` (prevents the 6 dB
  drop from amix halving inputs) onto the bass.
- **Length guard:** `ffprobe` both inputs; warn if durations differ (drift risk).
- Boundary clicks: windows are already padded ±100 ms in `detect`; natural room
  tone at the edges covers the transition. If ticks persist, a later refinement is
  trapezoidal gates or per-clip `afade` (noted, not built in V1).
- Output: `<track>_combined.wav`.

### `run`

Creates the track output dir, chains extract → detect → combine, leaves all
intermediates on disk for inspection and hand-correction.

## 7. Tooling

- **uv** — dependency + venv management (`uv sync`).
- **Runtime deps:** `typer`.
- **Dev deps:** `ruff`, `pytest`, `pre-commit`.
- **System prerequisites:** `ffmpeg`, `ffprobe` (documented, not pip-installed).
- **ruff** — lint + format, configured in `pyproject.toml`.
- **pre-commit** — ruff check + ruff format hooks.
- **justfile targets:** `install` (uv sync), `lint` (ruff check), `fmt`
  (ruff format), `test` (pytest), `check` (lint + test), and `run` / `extract` /
  `detect` / `combine` passthroughs.
- **GitHub CI** (`.github/workflows/ci.yml`): on push/PR → setup uv, `uv sync`,
  `ruff check`, `ruff format --check`, install ffmpeg (apt), `pytest`.

## 8. Testing strategy

- **Pure functions, unit-tested without ffmpeg:**
  - silencedetect stderr parser (feed sample text).
  - window pairing, including the unpaired trailing-start case.
  - gate-string builder.
  - `resolve_paths()` collection/track/artifact derivation.
- **Integration test** (marked, skipped when ffmpeg is absent): generate a tiny
  synthetic stereo WAV in-test → run `extract` → assert the bass channel is the
  L−R difference. Keeps CI honest without committing large audio files.

## 9. Later phases (not V1)

- `render` — CQT + scrolling waveform video, visuals driven by the bass while
  audio comes from the combined track (brief §4). The slow, fiddly part;
  isolated deliberately.
- Optional Silero VAD for labelled speech segments (brief §2).
- Trapezoidal / `afade` gate refinement if boundary clicks prove audible.
