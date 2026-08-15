from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bassify import combine as combine_mod
from bassify import detect as detect_mod
from bassify import encode as encode_mod
from bassify import extract as extract_mod
from bassify import metrics as metrics_mod
from bassify import remix as remix_mod
from bassify.pipeline import extract_batch, run_batch, run_pipeline
from bassify.render import render_batch, render_track
from bassify.slice import SliceSpec

DurationOpt = Annotated[
    float | None,
    typer.Option("--duration", help="Process only N seconds (ffmpeg -t)."),
]
StartOpt = Annotated[
    float | None,
    typer.Option("--start", help="Start offset in seconds (ffmpeg -ss)."),
]

app = typer.Typer(help="Isolate bass from stereo practice tracks.", no_args_is_help=True)


@app.command()
def extract(
    input_path: Path,
    output: Annotated[Path | None, typer.Option("-o", "--output")] = None,
    lowpass: Annotated[
        float,
        typer.Option(
            "--lowpass",
            help="Low-pass cutoff Hz to tame subtraction residue (default: 800). Use --no-lowpass to disable.",  # noqa: E501
        ),
    ] = extract_mod.DEFAULT_LOWPASS,
    no_lowpass: Annotated[
        bool,
        typer.Option(
            "--no-lowpass", help="Disable the low-pass filter entirely (overrides --lowpass)."
        ),  # noqa: E501
    ] = False,
    duration: DurationOpt = None,
    start: StartOpt = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing output.")] = False,
) -> None:
    """L-R subtraction -> mono bass WAV.

    Pass a single audio file, or a directory to extract every audio file in it
    (non-recursive, sorted, per-track error handling). Handy for regenerating
    bass.wav files after `just clean`. With a directory, -o/--output is ignored.
    """
    spec = SliceSpec(duration=duration, start=start)
    effective_lowpass = None if no_lowpass else lowpass
    if input_path.is_dir():
        extract_batch(input_path, lowpass=effective_lowpass, slice_spec=spec, force=force)
    else:
        extract_mod.extract_bass(
            input_path, output=output, lowpass=effective_lowpass, slice_spec=spec, force=force
        )


@app.command()
def detect(
    bass_path: Path,
    output: Annotated[Path | None, typer.Option("-o", "--output")] = None,
    threshold: Annotated[
        float, typer.Option("--threshold", help="silencedetect noise floor in dB.")
    ] = -40.0,
    min_gap: Annotated[
        float,
        typer.Option("--min-gap", help="Minimum quiet run (s) to count as a gap."),
    ] = 1.0,
    pad_start: Annotated[
        float,
        typer.Option(
            "--pad-start",
            help="Seconds to pad the gate START; negative pulls it later into the silence.",
        ),
    ] = 0.0,
    pad_end: Annotated[
        float,
        typer.Option(
            "--pad-end",
            help="Seconds to pad the gate END; negative pulls it earlier, before bass onset, to avoid leaking the downbeat.",  # noqa: E501
        ),
    ] = -0.05,
    min_riff: Annotated[
        float,
        typer.Option(
            "--min-riff",
            help="Merge windows separated by less than this many seconds of sound (removes gaps split by brief noise-floor blips).",  # noqa: E501
        ),
    ] = 0.5,
    count_in_original: Annotated[
        Path | None,
        typer.Option(
            "--count-in-original",
            help="Original mix; when given, refine each window end to sit just after the count-in, before the downbeat.",  # noqa: E501
        ),
    ] = None,
    keep_trailing: Annotated[
        bool,
        typer.Option(
            "--keep-trailing",
            help="Keep the final end-of-track silence window (normally dropped).",
        ),
    ] = False,
    duration: DurationOpt = None,
    start: StartOpt = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing output.")] = False,
) -> None:
    """Detect silence gaps in the bass -> windows JSON."""
    spec = SliceSpec(duration=duration, start=start)
    detect_mod.detect_windows(
        bass_path,
        output=output,
        threshold=threshold,
        min_gap=min_gap,
        pad_start=pad_start,
        pad_end=pad_end,
        min_riff=min_riff,
        slice_spec=spec,
        force=force,
        original_path=count_in_original,
        drop_trailing=not keep_trailing,
    )


@app.command()
def combine(
    bass_path: Path,
    original_path: Path,
    windows_path: Path,
    output: Annotated[Path | None, typer.Option("-o", "--output")] = None,
    duration: DurationOpt = None,
    start: StartOpt = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing output.")] = False,
) -> None:
    """Mix gated speech/count-ins onto the bass -> combined WAV."""
    spec = SliceSpec(duration=duration, start=start)
    combine_mod.combine_track(
        bass_path, original_path, windows_path, output=output, slice_spec=spec, force=force
    )


@app.command()
def remix(
    combined_path: Path,
    original_path: Path,
    output: Annotated[Path | None, typer.Option("-o", "--output")] = None,
    duration: DurationOpt = None,
    start: StartOpt = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing output.")] = False,
) -> None:
    """Build pannable stereo (L=combined, R=original right) -> remix WAV."""
    spec = SliceSpec(duration=duration, start=start)
    remix_mod.remix_track(combined_path, original_path, output=output, slice_spec=spec, force=force)


@app.command()
def encode(
    wav_path: Path,
    original_path: Path,
    output: Annotated[Path | None, typer.Option("-o", "--output")] = None,
    title_suffix: Annotated[
        str | None,
        typer.Option(
            "--title-suffix",
            help="Append a suffix to the iTunes title tag (e.g. '(Bass Only)').",
        ),
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing output.")] = False,
) -> None:
    """Encode a WAV to AAC/.m4a with original metadata + cover art."""
    encode_mod.encode_track(
        wav_path, original_path, output=output, force=force, title_suffix=title_suffix
    )


@app.command()
def run(
    input_path: Path,
    lowpass: Annotated[
        float,
        typer.Option(
            "--lowpass",
            help="Low-pass cutoff Hz for extract (default: 800). Use --no-lowpass to disable.",
        ),
    ] = extract_mod.DEFAULT_LOWPASS,
    no_lowpass: Annotated[
        bool,
        typer.Option(
            "--no-lowpass", help="Disable the low-pass filter entirely (overrides --lowpass)."
        ),  # noqa: E501
    ] = False,
    threshold: Annotated[
        float, typer.Option("--threshold", help="silencedetect noise floor in dB.")
    ] = -40.0,
    min_gap: Annotated[
        float, typer.Option("--min-gap", help="Minimum quiet run (s) to count as a gap.")
    ] = 1.0,
    duration: DurationOpt = None,
    start: StartOpt = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing output.")] = False,
) -> None:
    """Run the full pipeline: extract -> detect -> combine -> remix -> encode.

    Pass a single audio file to process one track, or a directory to process all
    audio files in it (non-recursive, sorted, per-track error handling).
    """
    spec = SliceSpec(duration=duration, start=start)
    effective_lowpass = None if no_lowpass else lowpass
    if input_path.is_dir():
        run_batch(
            input_path,
            lowpass=effective_lowpass,
            threshold=threshold,
            min_gap=min_gap,
            slice_spec=spec,
            force=force,
        )
    else:
        run_pipeline(
            input_path,
            lowpass=effective_lowpass,
            threshold=threshold,
            min_gap=min_gap,
            slice_spec=spec,
            force=force,
        )


@app.command()
def render(
    input_path: Path,
    preset: Annotated[str, typer.Option("--preset", help="draft | final | still")] = "final",
    duration: DurationOpt = None,
    start: StartOpt = None,
    res: Annotated[
        str | None, typer.Option("--res", help="Override resolution, e.g. 1920x1080.")
    ] = None,
    fps: Annotated[int | None, typer.Option("--fps", help="Override frames per second.")] = None,
    count: Annotated[
        int | None, typer.Option("--count", help="CQT transforms per frame (smoothness).")
    ] = None,
    crf: Annotated[
        int | None, typer.Option("--crf", help="x264 quality (lower=better, default 20).")
    ] = None,
    freq_range: Annotated[
        tuple[float, float] | None,
        typer.Option(
            "--freq-range",
            help="CQT bass framing LOW HIGH in Hz (default 65.41 261.63 = C2-C4).",
        ),
    ] = None,
    key: Annotated[
        str | None,
        typer.Option(
            "--key",
            help="Force key for label tiers (e.g. F, Bm). Wins over sidecar + auto-detect.",
        ),
    ] = None,
    no_waveform: Annotated[
        bool, typer.Option("--no-waveform", help="Drop the waveform strip.")
    ] = False,
    no_labels: Annotated[
        bool, typer.Option("--no-labels", help="Drop the note-name axis labels.")
    ] = False,
    font: Annotated[
        Path | None, typer.Option("--font", help="Override the label/title TTF.")
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing output.")] = False,
) -> None:
    """Render a bass_only.m4a to video (CQT + waveform) plus a thumbnail.

    Pass a single bass_only.m4a (the co-located bass.wav drives the visuals),
    or a directory to batch every bass_only.m4a that has a bass.wav beside it.
    """
    if preset not in ("draft", "final", "still"):
        raise typer.BadParameter("preset must be draft, final, or still")
    kwargs = dict(
        preset_name=preset,
        key=key,
        res=res,
        fps=fps,
        count=count,
        crf=crf,
        freq_range=freq_range,
        no_waveform=no_waveform,
        no_labels=no_labels,
        font=str(font) if font else None,
        force=force,
    )
    if input_path.is_dir():
        render_batch(input_path, **kwargs)
        return
    if preset == "final" and duration is None and start is None:
        print("note: full-length render can take minutes (CQT is ~0.5-2x realtime).")
        print("tip: add --duration 30 to preview a slice first.")
    render_track(input_path, **kwargs)


@app.command(name="measure-bleed")
def measure_bleed(
    collection_dir: Path,
    exclude_count_in: Annotated[
        bool,
        typer.Option(
            "--exclude-count-in",
            help="Drop count-in-refined windows (likely spoken narration between examples) from scoring, so the metric reflects guitar bleed during playing rather than voice bleed during narration.",  # noqa: E501
        ),
    ] = False,
) -> None:
    """Compare dirty vs clean residual guitar-bleed dB per track.

    Pass an out/<collection> directory (containing one subdirectory per
    track). Reuses each track's existing silence-windows JSON.
    """
    rows = metrics_mod.scan_collection(collection_dir, exclude_count_in=exclude_count_in)
    metrics_mod.print_report(rows)


if __name__ == "__main__":
    app()
