# Bassify — Audio Pipeline Design (V1)

Design spec for the first working version of `bassify`, a small CLI that isolates
the bass line from practice tracks recorded in a specific stereo format and
recombines spoken announcements / count-ins onto the bass.

Source brief: [`docs/bass-extraction-pipeline.md`](../../bass-extraction-pipeline.md).
This spec covers **V1 = audio only** (`extract`, `detect`, `combine`, `remix`,
`encode`). The video `render` stage (CQT + waveform) is deferred to a later phase.

---

## 1. Scope

**In scope (V1):**
- `extract` — isolate bass via L−R channel subtraction.
- `detect` — find silence gaps in the bass, emit JSON windows.
- `combine` — gate the original mix during those windows, mix onto the bass
  (mono).
- `remix` — build a pannable stereo file: L = combined (bass + speech), R =
  original right channel (mix minus bass). Balance becomes a bass-level knob.
- `encode` — compress both the mono combined WAV and the stereo remix WAV to
  shareable AAC/`.m4a`, carrying the original MP3's metadata and cover art
  forward.
- `run` — chain all stages, leaving inspectable intermediates on disk.
- Smart, reusable output-path layout mirroring the input collection.
- Interactive UAT checkpoints while developing each stage (see §8).
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

**Cover art:** these MP3s carry embedded cover art (an mjpeg stream). WAV/PCM has
no standard container for it, so ffmpeg drops the art automatically when encoding
the intermediates to `pcm_s24le` — nothing to strip. The audio-processing stages
pass `-vn` only to silence a harmless warning. Cover art matters at the **final
`encode` stage**, where carrying it (plus metadata) forward is *desirable* — see
§6 `encode`.

## 3. Architecture

Each stage is a function that builds an ffmpeg command, runs it via
`subprocess`, and returns paths/data. Stages are independently runnable and their
artifacts are inspectable on disk. Data flow:

```
input.mp3  ──extract──▶  <track>_bass.wav
<track>_bass.wav  ──detect──▶  <track>_silence_windows.json
<track>_bass.wav + input.mp3 + windows.json  ──combine──▶  <track>_combined.wav
<track>_combined.wav + input.mp3 (right ch)  ──remix──▶  <track>_remix.wav
<track>_combined.wav + <track>_remix.wav + input.mp3 (meta/art)  ──encode──▶  <track>_combined.m4a, <track>_remix.m4a
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
    combine.py              # gate-string builder, mix (mono)
    remix.py                # stereo: L=combined, R=original right channel
    encode.py               # AAC/.m4a encode, metadata + cover art carry-forward
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
bassify extract  <in.mp3>   [-o PATH] [--lowpass HZ] [--force]
bassify detect   <bass.wav>  [-o PATH] [--threshold -40] [--min-gap 1.0] [--force]
bassify combine  <bass.wav> <original.mp3> <windows.json> [-o PATH] [--force]
bassify remix    <combined.wav> <original.mp3> [-o PATH] [--force]
bassify encode   <audio.wav> <original.mp3> [-o PATH] [--force]
bassify run      <in.mp3>   [--lowpass HZ] [--threshold -40] [--min-gap 1.0] [--force]
```

- Typer chosen over argparse: subcommands map to typed functions, clean `--help`,
  autocompletion, standard in the modern uv/ruff ecosystem.
- `run` chains extract → detect → combine → remix → encode, writing every
  intermediate to the track's output dir so windows can be hand-corrected between
  runs and the final `.m4a` files are produced in one command.
- `remix` takes the original MP3 as its right-channel source.
- `encode` takes any produced WAV plus the original MP3 (as metadata / cover-art
  source); in `run` it is invoked for both the combined and remix WAVs.
- `-o` overrides a single artifact path for one-off use; default is the smart
  layout below.
- Every ffmpeg command is printed before execution.

## 5. Output-path layout

Path resolution is centralized in one `resolve_paths(input_path, out_root="out")`
helper used by every stage — no ad-hoc path building.

- **Collection dir** = the immediate parent directory name of the input file.
- **Per-track subdir** = the track basename (extension stripped).
- **Artifact names** = `<track>_bass.wav`, `<track>_silence_windows.json`,
  `<track>_combined.wav`, `<track>_remix.wav`, `<track>_combined.m4a`,
  `<track>_remix.m4a`.

Example — input `tracks/BluesBass/01_The Twelve Bar Blues Form.mp3`:

```
out/BluesBass/01_The Twelve Bar Blues Form/
  01_The Twelve Bar Blues Form_bass.wav
  01_The Twelve Bar Blues Form_silence_windows.json
  01_The Twelve Bar Blues Form_combined.wav
  01_The Twelve Bar Blues Form_remix.wav
  01_The Twelve Bar Blues Form_combined.m4a
  01_The Twelve Bar Blues Form_remix.m4a
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

### `remix`

- Builds a stereo WAV where **L = the combined track** (mono bass + speech +
  count-in) and **R = the original mix's right channel** (everything but bass).
- ffmpeg: take the combined mono as one input and the original as another, use
  `pan=stereo|c0=c0|c1=c1` sourcing L from the combined input and R from the
  original's channel 1 (via a `filter_complex` that maps the two sources).
- Balance becomes a bass-level knob: pan hard-left = isolated bass to learn,
  hard-right = backing track without bass to play along, center = both — mirroring
  the source format's intent in a single file.
- **Not a re-processable source:** its R is the original mix-minus-bass, so the
  L−R identity that `extract` relies on does *not* hold for this file. It is a
  listening/practice artifact only; never feed a remix back into `extract`.
- **Length guard:** combined WAV and original must match; reuse the `combine`
  duration check.
- Output: `<track>_remix.wav`.

### `encode`

- Encodes a given WAV to AAC in an `.m4a` container (`-c:a aac`, ~256 kbps), the
  modern, universally playable deliverable. In `run` it is invoked twice — once
  for `<track>_combined.wav` → `<track>_combined.m4a`, once for
  `<track>_remix.wav` → `<track>_remix.m4a`.
- Carries the **original MP3's** metadata (`-map_metadata` from the original) and
  embeds its cover art (map the original's mjpeg stream as an attached picture,
  `-disposition:v attached_pic`). The WAV supplies audio only; the original
  supplies tags + art.
- `-vn` is *not* used here — the art stream is intentionally kept.
- Output: `<input-wav-stem>.m4a` in the track dir.

### `run`

Creates the track output dir, chains extract → detect → combine → remix → encode
(twice), leaving all intermediates (bass WAV, windows JSON, combined WAV, remix
WAV) on disk for inspection and hand-correction alongside the final `.m4a` files.

## 7. Tooling

- **uv** — dependency + venv management (`uv sync`).
- **Runtime deps:** `typer`.
- **Dev deps:** `ruff`, `pytest`, `pre-commit`.
- **System prerequisites:** `ffmpeg`, `ffprobe` (documented, not pip-installed).
- **ruff** — lint + format, configured in `pyproject.toml`.
- **pre-commit** — ruff check + ruff format hooks.
- **justfile targets:** `install` (uv sync), `lint` (ruff check), `fmt`
  (ruff format), `test` (pytest), `check` (lint + test), and `run` / `extract` /
  `detect` / `combine` / `remix` / `encode` passthroughs.
- **GitHub CI** (`.github/workflows/ci.yml`): on push/PR → setup uv, `uv sync`,
  `ruff check`, `ruff format --check`, install ffmpeg (apt), `pytest`.

## 8. Testing strategy

- **Pure functions, unit-tested without ffmpeg:**
  - silencedetect stderr parser (feed sample text).
  - window pairing, including the unpaired trailing-start case.
  - gate-string builder.
  - remix pan/channel-map string builder.
  - `resolve_paths()` collection/track/artifact derivation.
- **Integration test** (marked, skipped when ffmpeg is absent): generate a tiny
  synthetic stereo WAV in-test → run `extract` → assert the bass channel is the
  L−R difference. Keeps CI honest without committing large audio files.

### Interactive UAT (during development)

Automated tests prove the plumbing; they cannot judge whether the bass *sounds*
right. Each stage therefore has a human-in-the-loop checkpoint against real
`tracks/BluesBass/` audio, run during development before the stage is considered
done:

- **After `extract`:** listen to `<track>_bass.wav`. Is the bass clean? Is the
  joint-stereo ghost distracting enough to need `--lowpass`, and at what cutoff?
- **After `detect`:** eyeball `<track>_silence_windows.json` against the track —
  do the windows land on the actual spoken/count-in gaps, with no musical rests
  caught and no announcements missed? Tune `--threshold` / `--min-gap`.
- **After `combine`:** listen to `<track>_combined.wav`. Do speech and count-ins
  reappear at the right spots with no clicks at boundaries and no 6 dB bass drop?
- **After `remix`:** listen to `<track>_remix.wav` while sweeping the balance.
  Does hard-left give isolated bass, hard-right the bass-less backing, center
  both? Are the two channels time-aligned?
- **After `encode`:** confirm `<track>_combined.m4a` and `<track>_remix.m4a` play
  everywhere they need to and carry the expected title/artist/album tags and
  cover art.

These checkpoints will appear as explicit UAT verification tasks in the
implementation plan, not as a final afterthought.

## 9. Later phases (not V1)

- `render` — CQT + scrolling waveform video, visuals driven by the bass while
  audio comes from the combined track (brief §4). The slow, fiddly part;
  isolated deliberately.
- Optional Silero VAD for labelled speech segments (brief §2).
- Trapezoidal / `afade` gate refinement if boundary clicks prove audible.
