from __future__ import annotations

from pathlib import Path

from bassify.ffmpeg import FfmpegError, run_ffmpeg, should_skip


def _args_with_art(wav: Path, original: Path, out: Path) -> list[str]:
    return [
        "-i",
        str(wav),
        "-i",
        str(original),
        "-map",
        "0:a",
        "-map",
        "1:v",
        "-c:a",
        "aac",
        "-b:a",
        "256k",
        "-c:v",
        "copy",
        "-disposition:v",
        "attached_pic",
        "-map_metadata",
        "1",
        str(out),
    ]


def _args_audio_only(wav: Path, original: Path, out: Path) -> list[str]:
    return [
        "-i",
        str(wav),
        "-i",
        str(original),
        "-map",
        "0:a",
        "-c:a",
        "aac",
        "-b:a",
        "256k",
        "-map_metadata",
        "1",
        str(out),
    ]


def encode_track(
    wav_path: Path,
    original_path: Path,
    output: Path | None = None,
    force: bool = False,
) -> Path:
    """Encode WAV -> AAC/.m4a, carrying original metadata + cover art if present."""
    out = output or wav_path.with_suffix(".m4a")
    out.parent.mkdir(parents=True, exist_ok=True)
    if should_skip(out, force):
        print(f"skip (exists): {out}")
        return out
    try:
        run_ffmpeg(_args_with_art(wav_path, original_path, out))
    except FfmpegError:
        print("no usable cover art; encoding audio + metadata only")
        run_ffmpeg(_args_audio_only(wav_path, original_path, out))
    return out
