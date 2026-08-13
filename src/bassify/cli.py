from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bassify import extract as extract_mod
from bassify.slice import SliceSpec

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
    duration: Annotated[
        float | None,
        typer.Option("--duration", help="Process only N seconds (ffmpeg -t)."),
    ] = None,
    start: Annotated[
        float | None,
        typer.Option("--start", help="Start offset in seconds (ffmpeg -ss)."),
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing output.")] = False,
) -> None:
    """L-R subtraction -> mono bass WAV."""
    spec = SliceSpec(duration=duration, start=start)
    effective_lowpass = None if no_lowpass else lowpass
    extract_mod.extract_bass(
        input_path, output=output, lowpass=effective_lowpass, slice_spec=spec, force=force
    )


if __name__ == "__main__":
    app()
