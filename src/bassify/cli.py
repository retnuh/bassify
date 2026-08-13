from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bassify import combine as combine_mod
from bassify import detect as detect_mod
from bassify import extract as extract_mod
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
    """L-R subtraction -> mono bass WAV."""
    spec = SliceSpec(duration=duration, start=start)
    effective_lowpass = None if no_lowpass else lowpass
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
    pad: Annotated[
        float,
        typer.Option(
            "--pad",
            help=(
                "Gate edge padding in seconds; 0 = exact detected silence, "
                "NEGATIVE pulls edges inward to avoid leaking the band at bass onset/offset."
            ),
        ),
    ] = 0.0,
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
        pad=pad,
        slice_spec=spec,
        force=force,
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


if __name__ == "__main__":
    app()
