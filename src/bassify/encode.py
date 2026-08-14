from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from bassify.ffmpeg import run_ffmpeg, should_skip


def _args_audio_tags(wav: Path, original: Path, out: Path) -> list[str]:
    """ffmpeg args: encode WAV -> AAC, carry text metadata from original, NO video stream."""
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


def _extract_cover_to_file(original: Path, dest: Path) -> bool:
    """Extract the attached picture from *original* to *dest* (JPEG).

    Returns True if extraction succeeded (art was present), False otherwise.
    Uses a bare subprocess call so a non-zero exit (no art) is a quiet False,
    not an exception that pollutes the terminal.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        str(original),
        "-an",
        "-c:v",
        "copy",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    return proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0


def _detect_image_format(data: bytes) -> int:
    """Return mutagen MP4Cover format constant from magic bytes."""
    from mutagen.mp4 import MP4Cover

    if data[:4] == b"\x89PNG":
        return MP4Cover.FORMAT_PNG
    return MP4Cover.FORMAT_JPEG


def _embed_covr(out: Path, art_data: bytes) -> None:
    """Write the iTunes covr atom into an existing .m4a file using mutagen."""
    from mutagen.mp4 import MP4, MP4Cover

    fmt = _detect_image_format(art_data)
    mp4 = MP4(str(out))
    mp4["covr"] = [MP4Cover(art_data, imageformat=fmt)]
    mp4.save()


def encode_track(
    wav_path: Path,
    original_path: Path,
    output: Path | None = None,
    force: bool = False,
    title_suffix: str | None = None,
) -> Path:
    """Encode WAV -> AAC/.m4a, carrying original metadata + cover art if present.

    Art is embedded as a proper iTunes covr atom (via mutagen) rather than as an
    ffmpeg video stream, so it appears in Apple Music and Finder.

    If *title_suffix* is given (e.g. "(Bass Only)"), the existing iTunes title tag
    is read and the suffix is appended, producing e.g. "Turnarounds (Bass Only)".
    When the source has no title tag, the suffix is written as the title on its own.
    """
    out = output or wav_path.with_suffix(".m4a")
    out.parent.mkdir(parents=True, exist_ok=True)
    if should_skip(out, force):
        print(f"skip (exists): {out}")
        return out

    # Step 1: encode audio + text metadata (no video stream).
    run_ffmpeg(_args_audio_tags(wav_path, original_path, out))

    # Step 2: try to extract cover art from the original source.
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_jpg = Path(tmpdir) / "cover.jpg"
        has_art = _extract_cover_to_file(original_path, tmp_jpg)
        art_data = tmp_jpg.read_bytes() if has_art else None
        if not has_art:
            print(f"no cover art in source; skipping covr for {out.name}")

        # Step 3: open MP4 once; set covr and/or title suffix as needed; save if changed.
        if art_data is not None or title_suffix is not None:
            from mutagen.mp4 import MP4, MP4Cover

            mp4 = MP4(str(out))
            changed = False
            if art_data is not None:
                fmt = _detect_image_format(art_data)
                mp4["covr"] = [MP4Cover(art_data, imageformat=fmt)]
                changed = True
            if title_suffix is not None:
                title_key = "\xa9nam"
                existing = mp4.tags.get(title_key, [None])[0] if mp4.tags else None
                if existing:
                    mp4[title_key] = [f"{existing} {title_suffix}"]
                else:
                    mp4[title_key] = [title_suffix.strip()]
                changed = True
            if changed:
                mp4.save()

    return out
