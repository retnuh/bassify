# Bass Extraction & Visualisation Pipeline

Project brief for a small CLI tool. Goal: take a stereo practice track where
**left = full mix** and **right = full mix minus bass**, extract the bass line,
re-add the spoken announcements and count-in clicks, and render a YouTube-ready
video whose visuals show only the bass.

Everything here is achievable with `ffmpeg` alone. A thin wrapper (Rust or
Python) is only needed for orchestration and batch processing.

---

## 1. Extract the bass (L − R)

The right channel is the mix without bass, so subtracting it from the full mix
leaves the bass.

```bash
# Bass as mono
ffmpeg -i in.mp3 -af "pan=mono|c0=c0-c1" bass.wav

# Or: L = bass, R = everything else
ffmpeg -i in.mp3 -af "pan=stereo|c0=c0-c1|c1=c1" -c:a pcm_s24le out.wav
```

`pan` does arbitrary linear combinations of channels. SoX equivalent:
`sox in.wav out.wav remix 1v1,2v-1`.

### Caveat: imperfect cancellation

Subtraction only cancels cleanly if both channels are sample-aligned and the
non-bass content is bit-identical. MP3 joint stereo does **not** encode L and R
independently, so expect a faint watery ghost of the other instruments —
most audible on cymbals and hi-hats, since high-frequency transients are where
joint-stereo coding diverges most. Vocals and guitars cancel better.

For learning a bass line this is fine. If the residue is distracting, low-pass
it — bass content sits well below most of the smearing:

```bash
ffmpeg -i in.mp3 -af "pan=mono|c0=c0-c1,lowpass=f=800" bass.wav
```

Try 500–1000 Hz. Lower kills more artifacts but strips the upper harmonics that
make note articulation legible. Tune by ear.

If the source came from separate stems rather than an encoded mix, cancellation
may be near-silent and no filter is needed. **Test the plain version first.**

---

### What was tried and rejected

Measured during the 2026-08-15 backstop investigation. Scripts live in
`experiments/`; re-run them before overturning any of this.

**`asupercut` is not a lowpass.** Its `cutoff` range is 20000-192000 Hz — it
cuts ultrasonic content. `asupercut=cutoff=800` fails a parameter check on
every track:

```
[Parsed_asupercut_0] Value 800.000000 for parameter 'cutoff' out of range [20000 - 192000]
```

The backstop is six chained `lowpass=f=800` stages (12 poles). Listening
tests (`experiments/backstop_variants.py`) compared 4, 8, 12 and 24 poles,
600/800/1200 Hz corners, a zero-phase variant, and -12/-20 dB high shelves.
Slope stops helping past 12 poles; a 600 Hz corner costs more bass than it
removes guitar; the zero-phase variant pre-rings audibly.

**Time-varying gain ("Approach B") — rejected, ≤1 dB.** Refitting `Ĥ[k]` per
10s block, and a better version anchored on runs of bass-free frames with
gains interpolated between anchors, both improved held-out leak by at most
1 dB on tracks 43, 40, 06 and 03 (`experiments/frame_leak.py`).

**NLMS adaptive FIR — rejected, worse than the baseline.** Tested at 8 kHz
with 512 taps, adapting continuously and gated to rests, at three step
sizes, single-pass and two-pass from converged taps
(`experiments/nlms_test.py`). It never beat the static projection and
usually lost to plain `L-R`. The reason is structural: NLMS minimises
*total* residual power, which here is dominated by the bass we are trying to
keep, so misadjustment stays large. Small step sizes converge too slowly;
large ones start eating bass (-0.7 dB on track 43 at mu=0.05). The shipped
bass-free-gated frequency-domain fit is the well-conditioned form of the
same idea.

**Why both adaptive approaches failed: the coherence ceiling.** Any
projection of R onto L can only remove content that is *correlated* between
channels. Energy-weighted `1 - coherence` over bass-free frames ranks tracks
by how much is cancellable at all, and that ranking predicts which tracks
the projection helps: 06 and 43 (ceiling ~-20 dB) gained 6-9 dB, while 40,
03 and 30 (~-11 dB) gained little or nothing. Their residual is decorrelated
— reverb tails, stereo-widened guitar — and no linear filter of R can touch
it. Improving those tracks needs something that is not a linear L/R
projection (spectral masking, or a separation model such as Demucs).

**Bass-free frames cluster at track edges.** On most of this collection the
bass plays continuously through the body, so frames detected as bass-free
are the count-in and the fade. Track 40's are `0.00-4.13s` and
`70.94-74.72s`, with two frames in the entire middle. Any metric scored on
bass-free content therefore measures the intro and the outro on most tracks
— the trap that sank the original silence-window metric and the frame-level
metric explored later. Only tracks with genuine mid-song rests (03, 43) can
be scored that way.

---

## 2. Detect the gaps (pure ffmpeg, no Python)

Spoken announcements and count-in clicks live in the gaps *between* riffs —
exactly where the bass track is silent. So gate on **bass silence**, not on
speech. One rule covers announcements, count-ins, and anything else in the gaps,
instead of a growing list of special cases.

```bash
ffmpeg -i bass.wav -af "silencedetect=noise=-40dB:d=1.0" -f null - 2> silence.txt
```

Output:

```
[silencedetect @ ...] silence_start: 12.284
[silencedetect @ ...] silence_end: 15.913 | silence_duration: 3.629
```

Parse with shell if you like:

```bash
grep -oP 'silence_(start|end): \K[0-9.]+' silence.txt
```

### Tuning notes

- **Threshold lower than for speech.** Bass notes ring and decay slowly; -30 dB
  treats a note tail as silence and opens the gate mid-decay. Start at
  **-40 to -45 dB**.
- **`d=1.0`** sets minimum quiet-run duration, so genuine musical rests inside a
  riff don't qualify as gaps. This is the knob that keeps rests from triggering.
- **Trailing silence prints no `silence_end`.** If the track fades out you get an
  unpaired `silence_start` — treat a missing end as "to end of file" rather than
  letting the pairing logic fall over.
- Pad each window ~100 ms at both ends.

Run this once and read the output before automating anything.

### Optional: Silero VAD

Only needed if announcements butt directly against riffs with no gap, or if you
want speech segments *labelled* rather than just located. Runs fine on Apple
Silicon CPU (~1 MB model, native arm64 torch wheels, or ONNX to skip torch).

```python
ts = get_speech_timestamps(
    wav, model, sampling_rate=16000,
    min_silence_duration_ms=700,   # 500–1000 groups words into sentences
    min_speech_duration_ms=400,    # discards blips
    speech_pad_ms=150,             # doubles as anti-click padding
    return_seconds=True,
)
```

Raise `threshold` to 0.6–0.7 if bass in vocal register causes false positives.
Run detection on the **original mix**, never on the subtracted track.

---

## 3. Recombine: bass + speech + count-in

Gate the original mix so it only passes during the detected quiet windows, then
mix it onto the bass track.

```bash
ffmpeg -i bass.wav -i original.wav -filter_complex \
"[1:a]volume='between(t,12.4,15.8)+between(t,47.1,51.3)':eval=frame[gap]; \
 [0:a][gap]amix=inputs=2:normalize=0[out]" \
-map "[out]" combined.wav
```

- The `between()` sum evaluates to 1 inside a window, 0 outside.
- `eval=frame` re-evaluates per frame instead of once at startup — **required**.
- `normalize=0` stops amix halving both inputs (otherwise the bass drops 6 dB).

### Clicks at boundaries

A hard 0→1 jump mid-waveform pops. Pad windows ~100 ms and let natural room tone
at the edges cover the transition. If it still ticks, build the gate as summed
trapezoids rather than rectangles, or trim speech clips separately with `afade`
in/out and `amix` those.

Also: a count-in overlapping the first bass note gets partially gated — pad the
window *end* generously and let the crossfade absorb the overlap.

**This is where the wrapper script earns its keep**: parse silencedetect output
into `(start, end)` pairs, emit the `between(t,a,b)+...` string, shell out to
ffmpeg. ~20 lines, handles a whole album without hand-typing timestamps.

---

## 4. Visuals

### Building blocks

```bash
# Scrolling waveform
showwaves=s=1280x720:mode=cline:colors=cyan
# modes: cline (centred), line, p2p, point

# Static waveform image of whole track
ffmpeg -i bass.wav -filter_complex "showwavespic=s=1280x720:colors=cyan" \
  -frames:v 1 wave.png

# Constant-Q — pitch is linear on screen, readable as notes
showcqt=s=1280x720:count=6
```

**Why CQT for bass:** a normal FFT spaces bins linearly, so the entire low end
gets crammed into a few pixels (E1→E2 spans 20 Hz; two octaves up spans 165 Hz)
while screen space is wasted on highs. Constant-Q makes each bin a fixed *ratio*
wider than the last, so every octave gets equal height and every semitone is the
same distance apart. A walking bass line draws even steps instead of a curve
that flattens out. Costs more CPU (low frequencies need long analysis windows),
and that same constraint means slightly softer time resolution down low.

### Combined layout

`asplit` is needed because each visualizer consumes the audio stream.

```bash
ffmpeg -i bass.wav -i combined.wav -filter_complex \
"[0:a]asplit=2[a1][a2]; \
 [a1]showcqt=s=1280x520:count=6[cqt]; \
 [a2]showwaves=s=1280x200:mode=cline:colors=cyan[wav]; \
 [cqt][wav]vstack=inputs=2[v]" \
-map "[v]" -map 1:a -c:v libx264 -pix_fmt yuv420p -c:a aac out.mp4
```

**Key trick:** visuals are driven by input 0 (`bass.wav`), audio comes from
input 1 (`combined.wav`) via `-map 1:a`. Input 0's audio is analysed and then
discarded. The display stays clean through spoken sections instead of lighting
up with vocal energy; during the count-in you hear clicks over a flat line,
which reads clearly as "nothing to play yet".

### Layout notes

- `vstack` requires **identical widths**; `hstack` requires identical heights.
- Separator gap: append `,pad=1280:530:0:10:black` to the CQT chain.
- Title: `,drawtext=text='Track — bass':fontcolor=white:fontsize=28:x=20:y=20`
- `fps=30` if scrolling looks choppy (default 25 is acceptable).
- **Keep `-pix_fmt yuv420p`** — without it some players show nothing.
- YouTube requires a video stream; a looped still image is the minimum.
- **The two inputs must be identical length and start together**, or visuals
  drift from audio. Check with
  `ffprobe -show_entries format=duration` on each.

---

## 5. Suggested tool shape

```
bassify <input.mp3> [--lowpass 800] [--threshold -40] [--min-gap 1.0]
```

Pipeline stages, each independently runnable and inspectable:

1. `extract` — L−R subtraction → `bass.wav`
2. `detect`  — silencedetect on bass → JSON of `(start, end)` windows
3. `combine` — gate original by those windows, mix onto bass → `combined.wav`
4. `render`  — CQT + waveform visuals from bass, audio from combined → `.mp4`

Emit the intermediate JSON to disk so windows can be hand-corrected before
combining. Always render a short test slice (`-t 15`) before a full pass —
CQT is slow enough that you don't want to find a sizing mistake four minutes in.

### Library options if going beyond shelling out

| | |
|---|---|
| **Python** | `soundfile` (libsndfile) → numpy; `bass = data[:,0] - data[:,1]` in one line. `pydub` or ffmpeg for MP3 decode. |
| **Rust** | `symphonia` for decoding (pure Rust: MP3/AAC/FLAC), `hound` for WAV I/O. `ffmpeg-next` for the full toolbox, but that means linking C libs. |
| **Onsets** | `librosa.onset.onset_detect` with `aggregate=np.median` if you ever want to target count-in clicks explicitly rather than via bass-silence. |

Shelling out to ffmpeg is likely sufficient for all four stages.
