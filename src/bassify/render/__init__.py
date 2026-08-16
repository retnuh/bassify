# src/bassify/render/__init__.py
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

from bassify.ffmpeg import ffprobe_duration, run_ffmpeg, should_skip
from bassify.render.filtergraph import FONT_SENTINEL, build_full_args, build_still_args
from bassify.render.key import resolve_key
from bassify.render.labels import build_axis_strip
from bassify.render.metadata import parse_track_meta
from bassify.render.overrides import get_override
from bassify.render.presets import PRESETS, apply_overrides
from bassify.render.tempo import detect_bpm
from bassify.render.thumbnail import build_thumbnail
from bassify.render.waveform import render_waveform_pic


def _read_tags(m4a: Path) -> dict[str, str]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format_tags=title,artist",
        "-of",
        "default=noprint_wrappers=1",
        str(m4a),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    tags: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if line.startswith("TAG:title="):
            tags["title"] = line[len("TAG:title=") :]
        elif line.startswith("TAG:artist="):
            tags["artist"] = line[len("TAG:artist=") :]
    return tags


def _extract_cover(m4a: Path, dest: Path) -> bool:
    cmd = ["ffmpeg", "-hide_banner", "-y", "-i", str(m4a), "-an", "-c:v", "copy", str(dest)]
    proc = subprocess.run(cmd, capture_output=True)
    return proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0


def _resolve_font(font: str | None) -> str:
    from importlib.resources import files

    return font or str(files("bassify.render.fonts") / "DejaVuSansMono.ttf")


def resolve_render_inputs(bass_only_m4a: Path) -> Path:
    """Return the co-located bass_clean.wav for a bass_only.m4a, or raise if missing."""
    bass_only_m4a = Path(bass_only_m4a)
    stem = bass_only_m4a.stem.replace("_bass_only", "_bass_clean")
    bass_wav = bass_only_m4a.with_name(stem + ".wav")
    if not bass_wav.exists():
        raise FileNotFoundError(
            f"No bass_clean.wav alongside {bass_only_m4a}. Run 'bassify run' first "
            f"(or 'bassify extract <dir>' to regenerate it). Expected: {bass_wav}"
        )
    return bass_wav


def _stem_and_suffix(bass_only_m4a: Path) -> tuple[str, str]:
    """Return (base stem without _bass_only or slice suffix, slice suffix)."""
    from bassify.slice import SliceSpec

    sfx = SliceSpec.from_filename(bass_only_m4a).suffix()
    stem = bass_only_m4a.stem.replace("_bass_only", "")
    if sfx and stem.endswith(sfx):
        stem = stem[: -len(sfx)]
    return stem, sfx


def _out_paths(bass_only_m4a: Path) -> dict[str, Path]:
    d = bass_only_m4a.parent
    stem, sfx = _stem_and_suffix(bass_only_m4a)

    def p(kind: str, ext: str) -> Path:
        return d / f"{stem}_{kind}{sfx}.{ext}"

    return {
        "render": p("render", "mp4"),
        "still": p("render_still", "mp4"),
        "thumb": p("thumbnail", "png"),
        "axis": p("axis", "png"),
        "wave": p("wave", "png"),
        "cover": p("cover", "jpg"),
        "windows": p("silence_windows", "json"),
    }


def render_track(
    bass_only_m4a: Path,
    preset_name: str = "final",
    *,
    key: str | None = None,
    res: str | None = None,
    fps: int | None = None,
    count: int | None = None,
    crf: int | None = None,
    freq_range: tuple[float, float] | None = None,
    no_waveform: bool = False,
    no_labels: bool = False,
    font: str | None = None,
    force: bool = False,
) -> Path:
    """Render one bass_only.m4a to video (+ thumbnail). Returns the primary output."""
    bass_only_m4a = Path(bass_only_m4a)
    bass_wav = resolve_render_inputs(bass_only_m4a)
    out = _out_paths(bass_only_m4a)
    primary = out["still"] if PRESETS[preset_name].still else out["render"]
    if should_skip(primary, force):
        print(f"skip (exists): {primary}")
        return primary
    preset = apply_overrides(
        PRESETS[preset_name],
        res=res,
        fps=fps,
        count=count,
        crf=crf,
        freq_range=freq_range,
        no_waveform=no_waveform,
        no_labels=no_labels,
    )
    font_path = _resolve_font(font)

    tags = _read_tags(bass_only_m4a)
    meta = parse_track_meta(bass_only_m4a, tags)

    # collection/source_stem: needed for the key override lookup below, and here
    # for finding the original source track BPM is detected from -- resolved
    # once, ahead of the thumbnail, since the thumbnail carries BPM too.
    collection = bass_only_m4a.parent.parent.name
    source_stem = _source_stem(bass_only_m4a)
    override = get_override(collection, source_stem)

    # BPM comes from the ORIGINAL track (drums present), not the isolated bass:
    # bass-only audio starves beat_track's onset detection. tracks/<collection>
    # is the CLI's own input convention (see `bassify run tracks/X`), the same
    # one metrics.py's original-track lookup relies on; a differently-organised
    # input just means no original is found and bpm stays None.
    original_path = next((Path("tracks") / collection).glob(f"{source_stem}.*"), None)
    bpm = detect_bpm(original_path, out["windows"]) if original_path is not None else None
    meta = replace(meta, bpm=bpm)

    # Cover art: needed for thumbnail (always) + logo/still. Synthesize if absent.
    if not _extract_cover(bass_only_m4a, out["cover"]):
        from PIL import Image

        Image.new("RGB", (1280, 720), (20, 20, 20)).save(out["cover"])
    build_thumbnail(out["cover"], out["thumb"], meta, font_path)

    if preset.still:
        run_ffmpeg(
            build_still_args(
                preset, cover_png=out["cover"], bass_only=bass_only_m4a, out_path=out["still"]
            )
        )
        return out["still"]

    # Resolve key, then label tiers.
    root_pc = resolve_key(key, override, bass_wav)

    axis_png = None
    if preset.labels:
        axis_png = build_axis_strip(
            out["axis"],
            width=preset.width,
            basefreq=preset.basefreq,
            endfreq=preset.endfreq,
            root_pc=root_pc,
            font_path=font_path,
        )
    wave_png = (
        render_waveform_pic(bass_wav, out["wave"], width=preset.width) if preset.waveform else None
    )

    duration = ffprobe_duration(bass_only_m4a)
    title_parts = [x for x in (meta.number, meta.name) if x]
    if meta.bpm is not None:
        title_parts.append(f"{round(meta.bpm)} BPM")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
        tf.write(" ".join(title_parts))
        title_file = Path(tf.name)

    # showcqt's axisfile= lives inside the -filter_complex string, where ffmpeg's
    # own graph parser treats an apostrophe (e.g. "Messin'") as a quote and
    # mangles the path — so the axis silently vanishes. Hand it a safe-named
    # tempfile copy instead (same reason title_file above is a tempfile).
    axis_safe: Path | None = None
    if axis_png is not None:
        with tempfile.NamedTemporaryFile("wb", suffix=".png", delete=False) as af:
            af.write(axis_png.read_bytes())
            axis_safe = Path(af.name)

    try:
        args = build_full_args(
            preset,
            bass_wav=bass_wav,
            bass_only=bass_only_m4a,
            wave_png=wave_png,
            cover_png=out["cover"] if preset.overlays else None,
            axis_png=axis_safe,
            title_file=title_file if preset.overlays else None,
            duration=duration,
            out_path=out["render"],
        )
        args = [a.replace(FONT_SENTINEL, font_path) for a in args]
        run_ffmpeg(args)
    finally:
        title_file.unlink(missing_ok=True)
        if axis_safe is not None:
            axis_safe.unlink(missing_ok=True)
    return out["render"]


def _source_stem(bass_only_m4a: Path) -> str:
    """The source track stem for sidecar lookup: strip _bass_only and any slice
    suffix so 'out/C/03_X/03_X_bass_only_d10s.m4a' -> '03_X'."""
    return _stem_and_suffix(bass_only_m4a)[0]


def render_batch(directory: Path, **kwargs) -> None:
    """Render every *_bass_only*.m4a under directory that has a co-located bass_clean.wav."""
    directory = Path(directory)
    m4as = sorted(directory.rglob("*_bass_only*.m4a"))
    rendered = skipped = failed = 0
    for m in m4as:
        bass_wav = m.with_name(m.stem.replace("_bass_only", "_bass_clean") + ".wav")
        if not bass_wav.exists():
            skipped += 1
            continue
        try:
            render_track(m, **kwargs)
            rendered += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR rendering {m.name}: {exc}")
            failed += 1
    print(
        f"render batch done: {rendered} rendered, {skipped} skipped (no bass_clean.wav), "
        f"{failed} failed"
    )
