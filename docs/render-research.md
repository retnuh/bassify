# Render Stage — Pre-Brainstorm Research

> Front-loaded research for the deferred V2 `render` stage (CQT + scrolling
> waveform video, YouTube-ready MP4). Compiled 2026-08-14 from the original
> project brief (§4) plus targeted web research. This is **input for a
> brainstorm**, not a design decision — it captures what's known so the
> brainstorm can focus on choices.

## What render must do (from the brief + built pipeline)

- Produce a YouTube-ready MP4 whose **visuals are driven by the isolated bass**
  (`bass.wav`) while the **audio is the `bass_only` mix** (bass + spoken
  count-ins). Visuals stay clean through speech; count-in reads as clicks over a
  flat line.
- Core trick (already proven in the brief): analyse input 0 (bass) for visuals,
  map audio from input 1 (`bass_only`) via `-map 1:a`; input 0's audio is
  discarded.
- The two inputs **must be identical length and start together** or visuals
  drift. Our length invariant (bass = bass_only = remix frame counts) already
  guarantees this — this is *why* we enforced it.
- Slice support: `--duration`/`--start` previews must render short test slices
  (CQT is slow; never find a sizing mistake 4 minutes in). Our filename-driven
  slice reconciliation already handles feeding sliced inputs downstream.

## Two render modes worth offering

1. **Full CQT render** — the real deliverable. Slow (minutes).
2. **Still-image fast mode** — loop cover art + audio. Renders in seconds, ~15-40MB.
   Good for iterating on the audio mix or testing the upload pipeline without
   waiting for CQT. Strong candidate for a `--mode still` / `--fast` flag.

---

## Part A — `showcqt` filter reference (the visual core)

CQT = constant-Q transform: **log-frequency**, so each octave occupies equal
pixel width and a walking bass line draws even steps instead of a flattening
curve. This is the whole reason to prefer it over a linear FFT for bass.

### Key parameters (ffmpeg 7.x/8.x)

- **`s`** (size, default 1920x1080) — width maps to frequency, must be even.
- **`basefreq` / `endfreq`** — frequency range across the frame width.
  Defaults span ~10 octaves (20Hz–20kHz). **For bass, narrow it.**
- **`fps`** (default 25) — directly scales render cost.
- **`count`** (default 6) — CQT transforms per output frame. Bass moves slowly,
  so `count=2` is often fine and much faster.
- **`tc` / `timeclamp`** (default 0.17) — the low-freq resolution knob. Higher =
  sharper pitch but temporal smear; lower = crisper transients, blurrier pitch.
  0.17 good for walking bass; ~0.08 for fast/slap; <0.05 too blurry.
- **`sono_g` / `bar_g`** (gamma) — lower = more contrast (helps faint bass).
- **`sono_v` / `bar_v`** (volume/brightness, default 16) — raise to 30-50 to
  brighten weak bass partials. Accept expressions using `f` (frequency).
- **`cscheme`** — six floats, **per stereo channel (L/R), NOT per frequency.**
  Common misconception. For freq-gradient color, post-filter with
  colorchannelmixer.
- **`axis`/`axisfile`/`fontfile`/`fontcolor`** — note-name labels (see gotcha).

### Bass frequency range (music anchors)

- E1 = 41.20 Hz (low string), E2 = 82.41, E3 = 164.8, E4 = 329.6, A4 = 440.
- Recommended: `basefreq=36 endfreq=500` (~3.8 octaves, ~505 px/octave at 1920w)
  for walking bass; `endfreq=1000` to include lower midrange context.
- Screen-x formula: `x = W * log(f/basefreq) / log(endfreq/basefreq)`.

### THE axis-label gotcha (important design fork)

**Built-in note labels break when you set custom `basefreq`/`endfreq`.** ffmpeg
docs: "Drawing with font file or embedded font is not implemented with custom
basefreq and endfreq; use axisfile option instead." Two ways to keep labels:

- **Option 1 (simplest): keep default range + `crop`.** Default spans 10 oct at
  ~192px/oct; crop the bass region and scale up. Preserves built-in labels.
  e.g. `showcqt=s=1920x1080,crop=576:1080:192:0,scale=1920:1080` ≈ C1–C4.
- **Option 2: custom range + generate an `axisfile` PNG** (Pillow: draw note
  names at computed x positions). More control, more code.

This is a real brainstorm decision: crop-default (less code, coarser control)
vs custom-range-with-axisfile (precise bass framing, needs a PNG generator).

### Performance reality

- showcqt is **CPU-only and single-threaded** — `-threads N` speeds only the
  H.264 encode, NOT the filter. Low frequencies need long FFT windows (~162ms
  at 41Hz), which is the structural slowness.
- Rough: 1080p/25fps ≈ 0.3-0.8x realtime (3-min song → 4-10 min). 720p ≈ 1-2x.
- Speed knobs: lower `s` (biggest lever — fewer freq bins), lower `count`,
  lower `fps`, narrower freq range. All trade quality for speed.
- **Implication:** a fast draft preset (720p, count=2, axis off) + a final
  preset (1080p, count=4, labels on) is the natural shape. Plus the still-image
  mode for pure audio iteration.

### Layout (from brief)

`vstack` CQT over showwaves needs **identical widths**; both heights even.
e.g. 1280x540 CQT + 1280x180 waves = 1280x720. `asplit` needed because each
visualizer consumes the audio stream.

---

## Part B — Encoding / delivery reference (YouTube-ready MP4)

### YouTube recommended H.264 specs (2025/2026)

- MP4 container, **moov atom at front** (`-movflags +faststart`, required).
- H.264 **High profile**, progressive, CABAC, 2 B-frames, **closed GOP =
  half the fps**, 4:2:0 chroma.
- Bitrate refs (SDR, standard fps): 1080p ≈ 8Mbps, 720p ≈ 5Mbps.
- Audio: AAC-LC, 48kHz, up to 384kbps (192k fine; `-c:a copy` if source already
  AAC — avoids a transcode generation).
- Color: BT.709 for SDR.
- **720p/30fps is the practical sweet spot** for a mostly-static visualization;
  1080p if the CQT fill is wide and detail matters.

### Use CRF, not target bitrate

For a low-motion visualization (flat dark areas + moving CQT), **CRF** lets the
encoder spend near-zero bits on static regions and concentrate on the CQT.
Target bitrate would waste/starve. **Default CRF 20** (expose `--crf`). YouTube
re-encodes anyway; CRF 20 is comfortably above the "indistinguishable after
their re-encode" threshold. ±6 CRF ≈ half/double bitrate.

### `-pix_fmt yuv420p` is mandatory

Filter/PNG sources can default to yuv444p, which most hardware decoders and
YouTube ingest reject (black screen / processing error). showcqt+showwaves+
vstack stay yuv420p natively, but set the flag explicitly as a contract.
Constraint: width & height must both be even.

### Reference commands

**Full CQT render (final):**
```
ffmpeg -i bass.wav -i bass_only.<ext> \
  -filter_complex \
    "[0:a]asplit=2[vis][ignore]; \
     [vis]showcqt=s=1280x540:fps=30:count=4:basefreq=36:endfreq=600:\
bar_g=2:sono_g=3:bar_v=30:sono_v=30:tc=0.17[cqt]; \
     [0:a]showwaves=s=1280x180:mode=cline:colors=cyan[waves]; \
     [cqt][waves]vstack[v]" \
  -map "[v]" -map 1:a \
  -c:v libx264 -profile:v high -level:v 4.0 -preset slow -crf 20 \
  -pix_fmt yuv420p -r 30 -g 15 -bf 2 \
  -c:a aac -b:a 192k -ar 48000 -movflags +faststart out.mp4
```
(Note: audio for visuals comes from input 0 = bass; output audio from input 1 =
bass_only via `-map 1:a`. The brief's key trick.)

**Still-image fast mode:**
```
ffmpeg -loop 1 -i cover.png -i bass_only.<ext> \
  -c:v libx264 -tune stillimage -preset ultrafast -crf 20 \
  -pix_fmt yuv420p -r 2 -c:a copy -shortest -movflags +faststart out_still.mp4
```
`-r 2` (image never changes → 2fps identical output, ~15x smaller video stream).

### Duration-sync gotchas

- With `asplit` driving both visualizers from the same audio, video ends when
  audio ends — no `-shortest` needed in the CQT path.
- Match filter `fps` to output `-r` or ffmpeg dup/drops frames at the tail.
- Keep the output-audio branch direct (no resample/delay after the split) to
  avoid PTS drift.
- Still-image mode: `-loop 1` is infinite → `-shortest` uses audio endpoint.
- ffmpeg 5.1+: prefer `-fps_mode vfr` over deprecated `-vsync`.

---

## Part C — Axis labels: crop vs axisfile (source-verified)

The central fork for keeping note-name labels while framing on bass.

### axisfile contract (from avf_showcqt.c source)

- PNG must be exactly **`W × axis_h`** px. `axis_h` auto = `width // 60`
  (1280→22, 1920→32, 3840→64), rounded even; override with `axis_h=N`.
- **Format: RGBA.** Alpha drives compositing per pixel:
  `alpha=0` → the CQT frequency color shows through; `alpha=255` → your pixel
  replaces it; between → linear blend.
- Wrong size = **silently bilinear-rescaled** (blurry labels), no warning.
- x-position of frequency f: `x = W * log2(f/basefreq) / log2(endfreq/basefreq)`
  — MUST use the exact `basefreq`/`endfreq` passed to showcqt.
- `axis=0` disables axisfile compositing entirely — keep `axis=1` (default).
- No existing published generator; we'd write it (novel ~80-line Pillow script).

### Pillow generator (validated approach)

RGBA image at exact `W×axis_h`; for each MIDI note in range compute x via the
formula, draw tick + label; piano convention (C notes emphasized/gold, others
grey, optional dim for sharps). Font discovery on macOS: try
`/System/Library/Fonts/SFNSMono.ttf` → `.../Supplemental/Courier New.ttf` →
`~/Library/Fonts/Hack-Regular.ttf`, else error (don't fall back to Pillow's
8px bitmap). Label density: at ~4 octaves/1920px, label only naturals (or only
C/E/G) + ticks for all semitones, else labels collide.

### Crop-default alternative (keeps built-in labels, less code)

Render full default range (has working labels), `crop` the bass region, `scale`
up. Crop math uses the source default constants
(`BASEFREQ=20.0152, ENDFREQ=20495.6`):
- 36–600 Hz at W=1920 → `crop=779:1080:162:0` then scale to 1920 = **2.47× upscale**
  (visibly softens labels).
- **Render at 4K then crop** → `crop=1558:2160:325:0` scale to 1920 = only 1.23×
  (much cleaner labels), at higher render cost.
- E1–G4 (bass-guitar range) ≈ `crop=624:1080:200:0`.

### Verdict for brainstorm

| | axisfile | crop-default |
|---|---|---|
| Label quality | crisp | blurry at 2.47× (1.23× if 4K) |
| Control (font/color/content) | full | none |
| Code | ~80 lines + Pillow dep | ~5 lines shell math |
| Font dependency | needs TTF | none |
| Risk | wrong formula = misaligned | rounding drift at crop edge |

Lean: **crop-default for V1 simplicity** (4K-render variant if labels look soft);
axisfile only if we want branded/custom labels. Pillow dep avoidable if we skip
axisfile.

## Part D — Visualizer menu + compositing (layout options)

### Visualizer filters, ranked for a bass learner video

- **showcqt** (primary) — pitch-mapped, note labels, walking bass = even steps.
  `tc` 0.3–0.5 favors pitch precision for sustained bass; 0.05–0.1 for pluck
  transients. `count` default 6 = 6 CQTs/frame (biggest cost knob; 3–4 fine).
- **showwaves** (best secondary) — time-domain amplitude; cheap (no FFT); shows
  attack timing + silence gaps the CQT can't. `mode=cline:scale=sqrt` reads well
  at 100–160px height. Complements CQT without redundancy.
- **showfreqs** — EQ-bars aesthetic; `fscale=log` gives bass more room; busier,
  overlaps CQT info. Third choice.
- **showspectrum** — waterfall; overlaps CQT (both freq-domain). Only for history view.
- **avectorscope / showspatial** — stereo Lissajous; **useless for mono bass** (flat
  line). Skip.
- **showwavespic** — single still image (thumbnail/reference only, not video).

### Compositing building blocks

- **Title/track name:** `drawtext` — for any non-trivial name use `textfile=`
  (write name to temp file) to dodge shell/filtergraph escaping hell. Box:
  `box=1:boxcolor=black@0.6:boxborderw=8`. macOS font e.g.
  `/System/Library/Fonts/Supplemental/Arial.ttf`.
- **Cover art:** separate input → `scale=60:60` → `overlay=x=W-w-10:y=10`.
  Opacity via `format=argb,colorchannelmixer=aa=0.7`. Can also be full-bg.
- **Progress bar / playhead:** two `color=` sources (bg + 2px fg),
  `overlay=x=t/DUR*W:shortest=1`. `DUR` from ffprobe, shell-substituted (not an
  ffmpeg var); `t` IS a runtime overlay var. Practical and cheap.

### Layout mechanics

- `vstack` needs identical **widths**; `hstack` identical **heights**; `xstack`
  arbitrary grid. Init every visualizer at the same width (e.g. 1920).
- `pad` adds gaps (vstack has no gap param) + letterboxing.
- **Every branch must be even-dimensioned and `format=yuv420p` before stacking**
  — showcqt defaults to yuv444p, sources vary; mismatched pixfmt breaks vstack.
  Append `,format=yuv420p` per branch.
- `asplit=N` is **mandatory** — each visualizer consumes the audio stream; you
  can't reference `[0:a]` twice.

### Reference "nice default" layout (from research, 1920×1080/25fps)

Title bar (80px: art + track name) / CQT (880px, labels) / showwaves strip
(120px) / 8px progress bar overlaid at bottom. Visuals from input 0 (bass),
audio from input 1 (mix) via `-map 1:a`, cover art input 2. Full working
filtergraph captured in research (asplit → showcqt+showwaves → vstack → title
vstack → progress overlay → format=yuv420p). Render ≈ 0.5–2× realtime on Apple
Silicon; levers: lower `count`, lower fps (24/20), `-preset fast`, `-threads 0`.

---

## Open questions for the brainstorm

Research has narrowed several of these to a leaning (noted); they're still
yours to decide in the brainstorm.

1. **Axis labels** (research: Part C) — crop-default (V1-simple, no Pillow dep;
   4K-render variant if labels soft) vs axisfile (crisp/branded, +Pillow, +~80
   lines). Lean crop-default for V1. DECIDE.
2. **Layout** (research: Part D) — CQT-only vs CQT+showwaves vs full
   title/art/progress layout. showwaves is the clear secondary. Configurable via
   flags, or one opinionated default? DECIDE scope.
3. **Presets** — draft (fast/720p, count 2-3, `-preset fast`), final
   (1080p/4K, count 6, labels), still-image (seconds). Research supports all
   three; DECIDE how many V1 ships.
4. **Resolution/fps default** — 720p30 sweet spot vs 1080p30 detail vs
   4K-render-then-downscale (needed if crop-default labels must be crisp). DECIDE.
5. **Cover art / branding** — reuse source `covr` (we already extract it in
   encode) as still-mode bg and/or corner logo in full layout. Mechanics known
   (Part D). DECIDE if V1.
6. **CLI shape** — `bassify render <track>` + `render` step in `run`? Flags:
   `--crf --res --fps --mode {full,still,draft} --freq-range --no-waveform
   --title`. DECIDE surface.
7. **Pillow dependency** — only needed IF axisfile chosen (Q1). Avoidable
   entirely with crop-default. Tied to Q1.
8. **Perf UX** — CQT is 0.5–2× realtime; always render a `--duration` slice
   first (our slice fix makes this correct now); progress via ffmpeg fps line;
   warn/estimate on full-length. DECIDE UX.
9. **Audio source for render** — confirm: visuals from `bass.wav` (clean through
   gaps), output audio from `bass_only` (or remix?) via `-map`. Which audio
   deliverable pairs with the video — bass_only mono, or the remix stereo? DECIDE.

## Sources (condensed)

- Project brief `docs/bass-extraction-pipeline.md` §4-5.
- ffmpeg showcqt: ffmpeg.org/ffmpeg-filters.html; avf_showcqt.c source;
  showcqt-js (mfcc64); hhsprings ffmpeg audio-visualization examples.
- YouTube upload specs: support.google.com/youtube/answer/1722171,
  /answer/4603579.
- CRF/rate-control: slhck.info/video/2017/02/24/crf-guide.html,
  /2017/03/01/rate-control.html.
- YouTube ffmpeg gists: mikoim/27e4e0dc..., wuziq/b86f8551...
- yuv420p compatibility: gist jaydenseric/220c785d...; HN 20036971.
- axisfile contract: github.com/FFmpeg/FFmpeg avf_showcqt.c; ffmpeg doxygen
  avf_showcqt.c/.h (7.0); hhsprings showcqt-crop-A0-C8 example.
- visualizers/compositing: ffmpeg filter docs (showcqt/showwaves/showspectrum/
  showfreqs/drawtext); hhsprings showcqt+showwaves overlay + progress-bar
  examples; OTTVerse stack-videos + drawtext guides.
- fonts (macOS): Pillow ImageFont docs; SFNSMono/Courier New/Hack.
- MIDI/freq table: newt.phys.unsw.edu.au/jw/notes.html.
