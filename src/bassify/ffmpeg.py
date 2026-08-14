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
