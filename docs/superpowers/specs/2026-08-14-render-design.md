# Bassify — Render Stage Design (V2)

Design spec for the `render` stage of `bassify`: turn the isolated bass and the
`bass_only` mix into a YouTube-ready MP4 whose visuals are driven by the bass,
plus a still-image fast mode and an upload thumbnail.

Source brief: [`docs/bass-extraction-pipeline.md`](../../bass-extraction-pipeline.md) §4.
Pre-brainstorm research: [`docs/render-research.md`](../../render-research.md).
Prior phase: [audio pipeline design](2026-08-13-bass-extraction-design.md) (V1,
merged). This spec covers **V2 = the video `render` stage only**; the audio
pipeline (`extract`→`encode`) is assumed already built and its outputs present.

---

## 1. Scope

**In scope (V2):**
- `render` — produce a 720p30 MP4: a constant-Q (`showcqt`) visualization framed
  on the bass range, stacked over a whole-track waveform strip with a sweeping
  playhead, with the track title and a small cover-art logo overlaid. Visuals are
  driven by the clean `bass.wav`; the audio the viewer hears is `bass_only`.
- Three presets: `draft` (fast, bare CQT), `final` (the deliverable), `still`
  (full-art loop for fast audio-mix checks).
- A separate `<track>_thumbnail.png` (full cover art with burned-in track number,
  name, and artist) for use as the YouTube thumbnail.
- Note-name axis labels on the CQT, generated as an `axisfile` PNG (Pillow) so the
  labels stay crisp while the frequency range is framed tightly on the bass.
- CLI `render` command accepting a single `bass_only.m4a` (error fast if the
  co-located `bass.wav` is missing) or a directory (render only the `bass_only.m4a`
  files that have a co-located `bass.wav`; skip the rest).
- Slice previews (`--duration`/`--start`) reusing the existing `SliceSpec`.
- A bundled default font (deterministic cross-platform), with a `--font` override.

**Out of scope (deferred / not V2):**
- Adding `render` to the `run` pipeline. `render` stays a standalone command; `run`
  remains audio-only and fast.
- Alternate/configurable layouts (multiple stacked visualizers, grid layouts). V2
  ships one opinionated layout.
- `showfreqs`/`showspectrum`/vectorscope visualizers (research Part D ranked these
  below CQT+waveform; vectorscope is useless for mono bass).
- 1080p/4K as the default (available via `--res`, but 720p30 is the default).
- Burning captions / chord charts / lyrics into the video.

## 2. Inputs and prerequisites

Render works **entirely off the audio pipeline's output folder** — it never
reaches back to the source track. This matters because the source and outputs live
in separate trees (in the real setup both are symlinks: `tracks/BluesBass` →
the source MP3 folder, `out/BluesBass` → a different `Remix` folder), and an
output folder may be handed around with no source tree present at all. The
`bass_only.m4a` deliverable is self-contained — it already carries the metadata
and cover art forwarded by `encode` — so it, not the source MP3, is render's
primary input.

`render`'s argument is the **`bass_only.m4a`** file (or a directory to batch —
see below). From it render resolves everything:

- **`bass_only.m4a`** (the argument) — bass + spoken count-ins, mono. Supplies:
  - **the audio the viewer hears** (`-map` to this input); confirmed choice —
    matches the "learn the bass line" purpose; the remix stereo pan is a player
    feature, not something to bake into one video.
  - **metadata** — `title` and `artist` tags (forwarded from the source by
    `encode`).
  - **cover art** — the embedded `covr` atom, extracted once and reused as the
    corner logo, the thumbnail base, and the still-mode background.
- **`bass.wav`** — clean isolated bass, located **alongside** `bass_only.m4a` in
  the same directory (same stem, `_bass<sfx>.wav`). **Drives all visuals.** Chosen
  because it is silent through spoken gaps, so the CQT and waveform stay clean
  while count-ins read as clicks over a flat line. This is the one WAV render
  depends on.

**Metadata catch (verified against a real `bass_only.m4a`):**
- The track **number is not in the m4a tags** (`trkn` is empty), so render parses
  it from the **filename stem** (`03_Turnarounds_bass_only…` → `03`). Number from
  filename; name/artist/art from tags. See §5.
- The `title` tag reads `Turnarounds (Bass Only)` — `encode` appends the
  `(Bass Only)` marker, and render **keeps it verbatim**: that is literally what
  the file is, and the marker belongs on the title overlay and thumbnail.

**Length invariant (why it matters here):** the audio pipeline guarantees
`bass.wav`, `bass_only`, and `remix` have identical frame counts. Render depends
on this: visuals come from the `bass.wav` input and audio from the `bass_only`
input; if they differed in length the visuals would drift against the sound.
Render does not re-establish this; it relies on it and asserts video duration ==
audio duration in tests.

**Prerequisite handling:**
- **Single file**, the co-located `bass.wav` missing → **error fast** with a clear
  message naming the expected path, e.g.
  `No bass.wav alongside <bass_only.m4a>. Run 'bassify run' first.` Render never
  auto-runs the (slow) audio pipeline.
- **Directory batch** → scan the tree for `*_bass_only*.m4a`, but render only those
  with a co-located `bass.wav`. Files without it are **skipped** (reported in a
  one-line summary), not errors. Explicit single-file requests explain themselves;
  batch just does what it can.

**Interaction with `just clean`:** `clean` deletes all `*.wav` under `out/`,
including `bass.wav`. `bass_only.m4a` survives (it is not a WAV), but the CQT
visual source does not. Rendering after a `clean` therefore requires re-running
the audio pipeline first to regenerate `bass.wav`. This is the expected behavior
(WAVs are treated as regenerable intermediates); render's error message points the
user at it.

## 3. Architecture

`render` is the first subpackage in the (so-far flat) `src/bassify/`. Render is
materially more complex than the audio stages — it involves two Pillow image
generators, a multi-pass ffmpeg orchestration, a preset table, and a filtergraph
builder — so it is split by responsibility rather than crammed into one module.

```
src/bassify/render/
  __init__.py      render_track()  — public orchestrator
  metadata.py      TrackMeta + parse: number (filename), name/artist (tags)
  labels.py        build_axis_strip() → RGBA axisfile PNG (Pillow)   [#1 risk]
  thumbnail.py     build_thumbnail() → full-art + burned title PNG
  waveform.py      render_waveform_pic() → whole-track showwavespic PNG (ffmpeg)
  filtergraph.py   build_filtergraph(preset, inputs) → str  (pure; no subprocess)
  presets.py       PRESETS: draft / final / still  (frozen knob bundles)
  fonts/           bundled default TTF (OFL/Apache) as a package resource
```

**Boundaries (each unit testable in isolation):**
- `filtergraph.py` is **pure string-building** — given a preset and resolved input
  paths/dimensions it returns the `-filter_complex` text. No ffmpeg call. The
  research's most error-prone details (per-branch `format=yuv420p`, `asplit`, even
  dimensions, `-map 1:a`, the overlay playhead x-formula) live here where fast
  unit tests can assert them.
- `labels.py` takes `(basefreq, endfreq, width, axis_h)` and returns a PNG. Its
  x-position formula is the single biggest flagged risk; it is unit-tested against
  hand-computed note positions **without rendering any video**.
- All Pillow code (`labels`, `thumbnail`) is isolated from all ffmpeg code
  (`waveform`, the main render in `__init__`).

**Data flow — `render_track(bass_only_m4a, preset, slice_spec, overrides, ...)`:**

```
1. resolve inputs   → bass_only.m4a (arg), co-located bass.wav; error if wav absent
2. metadata.parse() → TrackMeta(number ← filename, name/artist ← m4a tags)
3. pre-passes (only those the preset needs):
     extract covr from bass_only.m4a → <track>_cover.png  (logo + thumbnail + still bg)
     labels.build_axis_strip()       → <track>_axis.png   (final only)
     waveform.render_waveform_pic()  → <track>_wave.png   (final/draft; not still)
4. thumbnail.build_thumbnail()       → <track>_thumbnail.png   (always)
5. filtergraph.build_filtergraph()   → -filter_complex string for the preset
6. run ffmpeg → <track>_render.mp4   (or <track>_render_still.mp4)
     visuals ← bass.wav input, audio ← bass_only.m4a input (-map)
     progress streamed to the user
```

The `<track>` stem for output names is derived from the `bass_only.m4a` filename
(dropping the `_bass_only<sfx>` portion), so render's artifacts sit in the same
directory with matching names and slice suffix.

Each stage function builds an ffmpeg (or Pillow) operation and runs it, mirroring
the audio pipeline's "build command, run via subprocess, return paths" pattern.

## 4. Layout

One opinionated layout. Title and cover-art logo are **overlays on the CQT**, not
their own stacked bands, so no vertical space is wasted on a title bar. The
playhead is a moving line over the waveform strip, so no separate progress bar is
needed.

```
┌───────────────────────────────┐
│ [logo]  03  Turnarounds        │  drawtext (title) + overlay (corner art)
│                                │
│      showcqt (bass range)      │  bass-framed CQT, axisfile note labels
│      ▂▄█▆▄▂                     │
│   E1  A1  E2  A2  E3  A3  E4    │
├───────────────────────────────┤
│ ▁▂▃▅▇▅▃▂▁ │ ▂▃▁  waveform      │  showwavespic still + sweeping playhead line
└───────────────────────────────┘
              1280×720
```

- **CQT + waveform** are two different filters, so they are genuinely stacked
  (`vstack`, identical widths, even heights, each `,format=yuv420p` before the
  stack).
- **Title** — `drawtext` overlaid on the dark top of the CQT. Non-trivial names
  are written to a temp `textfile=` to avoid filtergraph escaping issues, with a
  semi-transparent box for legibility.
- **Corner logo** — cover art scaled small (~60px), semi-transparent, overlaid
  top-corner.
- **Waveform strip** — `showwavespic` renders the *whole track* as one static
  image in a quick pre-pass (seconds), saved as `<track>_wave.png`. The main
  render loops that PNG as the bottom strip and overlays a vertical playhead line
  at `x = t/DUR*W` (`DUR` from `ffprobe`, shell-substituted; `t` is the runtime
  overlay variable). One strip does waveform + progress together.

## 5. Metadata

Three fields feed both the video title overlay and the thumbnail. All are read
from the **`bass_only.m4a`** (tags + filename) — render never reads the source
MP3. Sources:

- **Track number** ← leading digits of the filename stem
  (`03_Turnarounds_bass_only…` → `03`). This is the **only** source: verified that
  neither the source MP3 nor the `bass_only.m4a` carries a track-number tag
  (`trkn`/`track` empty), so the filename convention is authoritative and `encode`
  has nothing to carry forward. (If a future source set does tag track numbers,
  `encode` could forward it and render could prefer the tag; not needed now.)
- **Track name** ← the m4a's `title` tag (e.g. `Turnarounds (Bass Only)`, kept
  verbatim — see §2); fall back to the filename name portion only if the tag is
  absent. The tag is the better source: e.g. track 08's file is
  `08_Uptown Up_Uptown Down…` but its title tag is `Uptown Up/Uptown Down` — the
  filename `_` stands in for a slash that is illegal in filenames.
- **Artist** ← the m4a's `artist` tag (e.g. `Ed Friedland`).

**Parsing rules:**
- The number is the leading digits of the stem, split on the **first `_`**. The
  remainder (with `_` → space) is only the filename-name *fallback* for the title.
- This `NN_Name` convention holds for the current track set; it can be adjusted
  later if other sets differ.

**Missing-field policy: skip, never error.** A missing number, title, or artist
just omits that line. The thumbnail and title overlay always render with whatever
fields are present.

## 6. Thumbnail

A standalone `<track>_thumbnail.png` (1280×720, 16:9) for uploading as the
YouTube thumbnail — YouTube ignores an MP4's embedded poster, so a separate file
is the right deliverable. The same full-art image is the still-mode background.

Layout: full cover art fills the frame; a centered text block, anchored roughly
two-thirds down, reads (top to bottom):

```
              03            ← track number (medium)
         Turnarounds        ← track name (large, bold)
         Ed Friedland       ← artist (smaller)
```

- Text horizontally centered; block anchored ~2/3 down the frame.
- A semi-transparent scrim behind the text block (cover art can be busy) for
  legibility.
- Any missing line is omitted and the remaining lines re-centered.

## 7. Presets

Three frozen preset bundles in `presets.py`. Flags patch individual knobs on top
of the chosen preset.

| knob                | draft            | final                     | still                          |
|---------------------|------------------|---------------------------|--------------------------------|
| purpose             | fast preview     | the deliverable           | audio-mix check                |
| resolution          | 1280×720         | 1280×720                  | 1280×720                       |
| fps                 | 30               | 30                        | 2                              |
| CQT `count`         | 2                | 4                         | — (no CQT)                     |
| axis labels         | off              | on (axisfile)             | —                              |
| waveform strip      | off              | on                        | —                              |
| title / logo        | off              | on                        | on the thumbnail/still bg      |
| x264 `-preset`      | fast             | slow                      | ultrafast + `-tune stillimage` |
| crf                 | 20               | 20                        | 20                             |
| visual              | bare CQT         | CQT + waveform + overlays | full-art still loop            |
| speed               | ~2–4× realtime   | ~1–2× realtime            | seconds                        |

**Shared across all presets** (encoding contract, from research Part B):
- `-pix_fmt yuv420p` (mandatory; filter/PNG sources can default to yuv444p, which
  YouTube ingest rejects — set explicitly as a contract).
- `-movflags +faststart` (moov atom at front).
- `-c:v libx264 -profile:v high`, closed GOP (`-g` = fps/2).
- `-c:a aac -b:a 192k -ar 48000`.
- CQT bass framing default `basefreq=36 endfreq=600` (~3.8 octaves, walking-bass).
- Visuals from `bass.wav`; audio from `bass_only`.

`count` is a motion-smoothness knob (CQT recomputations per frame), independent of
audio quality; 4 is ample for slowly-moving bass. `draft` drops the two
slow/complex pieces (axisfile Pillow generation + waveform pre-pass + overlays) to
render raw CQT quickly for checking freq range and framing.

## 8. CLI

```
bassify render <bass_only.m4a | dir> [options]
```

The argument is the **`bass_only.m4a`** deliverable (render finds the co-located
`bass.wav`), or a **directory** to batch over every `*_bass_only*.m4a` found in the
tree (those with a co-located `bass.wav`; others skipped). Render never takes or
reads the source MP3.

| flag                     | default        | meaning                              |
|--------------------------|----------------|--------------------------------------|
| `--preset {draft,final,still}` | `final`  | knob bundle                          |
| `--duration N`           | none           | slice preview length (`SliceSpec`)   |
| `--start N`              | none           | slice start offset (`SliceSpec`)     |
| `--res WxH`              | preset         | override resolution                  |
| `--fps N`               | preset         | override fps                         |
| `--count N`             | preset         | CQT smoothness                       |
| `--crf N`               | 20             | quality/size                        |
| `--freq-range LOW HIGH` | 36 600         | CQT bass framing (two values)        |
| `--no-waveform`         | off            | drop the waveform strip              |
| `--no-labels`           | off            | drop axis labels                     |
| `--font PATH`           | bundled font   | override the label/title font        |
| `--force`               | off            | overwrite existing output            |

- The preset supplies defaults; flags override individual knobs (e.g.
  `render --preset draft --res 1920x1080`).
- `render` is **standalone** — not part of `run`.
- **Prerequisites:** single file with no co-located `bass.wav` → error fast;
  directory batch → skip `bass_only.m4a` files lacking a `bass.wav` (one-line
  summary).

**Artifacts produced** (under `out/<collection>/<track>/`, slice suffix applied
where relevant): `<track>_render.mp4` (final/draft), `<track>_render_still.mp4`
(still), `<track>_thumbnail.png` (always). Intermediates `<track>_axis.png`,
`<track>_wave.png`, `<track>_cover.png` live alongside and are removed by
`just clean`.

## 9. Fonts

Text rendering (axis labels, thumbnail/title) needs a TTF. Rather than probe
platform font paths (fragile; the research's macOS paths don't exist on CI Linux),
render **bundles a default font** as a package resource in
`src/bassify/render/fonts/` (an OFL- or Apache-licensed face; license/attribution
noted in the repo). Resolution order: `--font PATH` (explicit) → bundled font.
This yields deterministic output across macOS and Linux CI. The same bundled font
is used by the tests, so no test needs a system font.

## 10. Performance UX

The full CQT render is CPU-only, single-threaded, and roughly 0.5–2× realtime
(research Part A), so a full-length render can take minutes.

- **Streamed progress:** the ffmpeg progress line (`frame= time= fps=`) is shown
  so the render is visibly working, not a silent hang.
- **Slice-first nudge:** when running `--preset final` with no `--duration`, print
  a one-line up-front time estimate and a tip:
  `tip: add --duration 30 to preview a slice first`. The slice fix in the audio
  pipeline makes sliced previews correct (all inputs sliced consistently), so
  previewing a 30s slice before committing to a full render is the intended
  workflow.

## 11. Testing

Mirrors the existing split: pure unit tests run without ffmpeg; ffmpeg/Pillow
tests are marked `integration`.

**Pure unit tests (fast, no ffmpeg):**
- `test_metadata.py` — number from filename stem (`03_x_bass_only` → `03`);
  title/artist read from the m4a tags; `(Bass Only)` suffix kept verbatim;
  slash-title case (track 08 `Uptown Up/Uptown Down`); missing artist tag → line
  skipped, no error; filename with no leading number → number skipped.
- `test_filtergraph.py` — builder emits `format=yuv420p` on every branch; `asplit`
  present; even dimensions; `-map 1:a` for audio; the playhead overlay x-formula;
  each preset yields the expected graph shape. Pure string assertions.
- `test_labels.py` — the #1 risk: x-position formula
  `x = W·log2(f/basefreq)/log2(endfreq/basefreq)` verified against hand-computed
  positions (basefreq → x=0; endfreq → x=W; one octave up → known x). Output PNG
  is exactly `W×axis_h` RGBA. No video rendered.
- `test_presets.py` — flag overrides patch the preset correctly (`--no-waveform`
  drops the branch; `--res`/`--fps`/`--count` override).

**Integration tests (`integration` marker; ffmpeg + Pillow):**
- `test_render_integration.py` — first run the audio pipeline on the synthetic
  3-segment source already used by `test_integration.py` to produce the
  co-located `bass.wav` + `bass_only.m4a` pair (with a `title`/`artist` tag), then
  render **from the `bass_only.m4a`**:
  - `still` preset → valid MP4 with an audio stream and `+faststart`.
  - `final` preset on a short `--duration` slice (keeps CQT cost tiny) → valid
    MP4 with video + audio streams, `yuv420p`, correct duration.
  - thumbnail PNG produced at 1280×720.
  - **length-sync assertion:** rendered video duration == audio duration (the
    drift guard the whole length invariant exists to protect).
  - **error-fast case:** pointing render at a `bass_only.m4a` with no co-located
    `bass.wav` exits non-zero with the expected message.
- Tests use the bundled font, so they are deterministic on macOS and CI Linux.

## 12. Dependencies

- **Pillow** — new dependency, required for `labels.py` (axisfile generation) and
  `thumbnail.py`. Added to `pyproject.toml`.
- **ffmpeg / ffprobe** — already required by the audio pipeline (showcqt,
  showwavespic, drawtext, overlay, x264/aac all ship with standard ffmpeg 7.x/8.x).
- No new runtime deps beyond Pillow.
