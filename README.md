# bassify

Isolate the bass line from a stereo practice track, and turn it into a
YouTube-ready practice video: isolated bass audio, spoken count-ins and
narration re-added, and a scrolling note-name visualization synced to the
music.

## Origin

Built against Hal Leonard's
[**Blues Bass: A Guide to the Essential Styles and Techniques**](https://www.halleonard.com/product-family/PC790/blues-bass-a-guide-to-the-essential-styles-and-techniques)
by Ed Friedland — a bass method book whose companion audio is mastered in a
particular way that makes isolation possible: **left channel = full mix**,
**right channel = full mix minus bass**. That's the format this tool expects.
Point it at a differently-mastered track and the extraction won't work.

## What it does

Given a stereo track in that L/full, R/no-bass format:

1. **Extracts the bass.** A naive `L − R` gets most of the way there, but
   drifts on real recordings (channel delay, level/phase mismatch). The real
   extraction time-aligns the channels and fits a per-frequency complex gain
   from the track's own bass-free moments — see
   [`docs/bass-extraction-pipeline.md`](docs/bass-extraction-pipeline.md) for
   the full design and the DSP approaches that were tried and rejected.
2. **Detects the count-in and narration windows** (silence-gap detection on
   the isolated bass) so the next step knows what to preserve.
3. **Re-combines** the isolated bass with the original spoken narration and
   count-in clicks — losing those would make the practice track useless, even
   though the isolation itself only needs the music.
4. **Renders a video**: a scrolling Constant-Q visualization with a key-aware
   note-name axis (root gold, the blue note actually blue, everything else
   sized and shaded by how in-the-scale it is), an optional waveform strip,
   and a title card with the track's cover art, name, artist, and detected
   tempo.

## Quick start

```bash
uv sync
uv run bassify run tracks/<collection>/01_Some_Song.mp3 --render
```

That single command produces the isolated bass audio, the narration-restored
mix, and the finished video. Drop `--render` to stop after audio (you can
render later with `bassify render`); point `run` at a whole collection
directory instead of one file to batch it, sorted, with per-track error
isolation — one bad track doesn't stop the rest.

## Layout convention

```
tracks/<collection>/<track>.mp3      # source audio, L=full R=no-bass
out/<collection>/<track>/            # everything this tool produces
    <track>_bass.wav                 # naive L-R (detect.py's click detection depends on this exact file)
    <track>_bass_clean.wav           # the real extraction
    <track>_bass_only.{wav,m4a}      # bass_clean + narration/count-in restored
    <track>_remix.{wav,m4a}          # same L/full R/no-bass shape as the source, but with the isolated bass_only in place of the noisy left channel
    <track>_render.mp4               # the finished video
    <track>_thumbnail.png            # title card
    <track>_silence_windows.json     # detected narration/count-in windows
data/<collection>.yaml               # optional per-track overrides (key, etc.)
```

`collection` is just the containing directory name (`tracks/BluesBass` →
`out/BluesBass`) — there's no registry to update, drop a new folder of source
tracks in and run against it.

## Commands

`bassify --help` lists everything; the ones you'll actually reach for:

| Command | Does |
|---|---|
| `run <path> [--render]` | The full pipeline, one file or a whole directory. Add `--render` to also produce the video in the same pass, interleaved per track. |
| `render <path>` | Video + thumbnail only, from an already-extracted `bass_only.m4a`. `--preset draft` for a fast encode with no labels/waveform/overlays, `--duration 30` to preview a slice. |
| `measure-bleed <out/collection>` | How much non-bass content survived isolation, scored against the original track — the metric used to judge and compare extraction quality. |

`extract` / `detect` / `combine` / `remix` / `encode` run the individual
pipeline stages on their own, useful when only one stage needs re-running
(e.g. after tuning a filter) rather than the whole track.

## Requirements

- Python 3.13, via [`uv`](https://docs.astral.sh/uv/)
- `ffmpeg`/`ffprobe` on `PATH`, with `showcqt` (standard in any recent build)
- [`just`](https://github.com/casey/just), for the dev commands below

## Development

```bash
just check   # lint + format-check + full test suite — what CI runs
just clean   # remove generated WAV/PNG scratch under out/
```

See `AGENTS.md` for the project's formatting convention (always `ruff`, never
by hand) and `docs/bass-extraction-pipeline.md` for the original technical design
created chatting with Claude.AI while waiting at the bus stop!  What a world we live
in these days.

## License

Public domain — see [`UNLICENSE`](UNLICENSE).
