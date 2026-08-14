from __future__ import annotations

from pathlib import Path

from bassify.render.presets import RenderPreset

WAVE_STRIP_H = 80  # fixed waveform strip height; CQT takes the rest of preset.height

# Sentinel the orchestrator replaces with the resolved font path (keeps this
# module font/filesystem-agnostic and purely string-building).
FONT_SENTINEL = "__BASSIFY_FONT__"


def _cqt_height(preset: RenderPreset) -> int:
    h = preset.height - (WAVE_STRIP_H if preset.waveform else 0)
    return h - (h % 2)


def _output_args(preset: RenderPreset, out_path: Path) -> list[str]:
    return [
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-preset",
        preset.x264_preset,
        "-crf",
        str(preset.crf),
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "1",  # limited range → yuv420p not yuvj420p
        "-r",
        str(preset.fps),
        "-g",
        str(preset.fps // 2),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        "-shortest",
        str(out_path),
    ]


def build_full_args(
    preset: RenderPreset,
    *,
    bass_wav: Path,
    bass_only: Path,
    wave_png: Path | None,
    cover_png: Path | None,
    axis_png: Path | None,
    title_file: Path | None,
    duration: float,
    out_path: Path,
) -> list[str]:
    w = preset.width
    cqt_h = _cqt_height(preset)
    axis = f":axis_h=48:axisfile={axis_png}" if (preset.labels and axis_png is not None) else ""
    parts = [
        f"[0:a]showcqt=s={w}x{cqt_h}:fps={preset.fps}:count={preset.count}:"
        f"basefreq={preset.basefreq:g}:endfreq={preset.endfreq:g}:"
        f"bar_g=2:sono_g=3:bar_v=30:sono_v=30:tc=0.17{axis},format=yuv420p[cqt]"
    ]
    inputs = ["-i", str(bass_wav), "-i", str(bass_only)]
    next_idx = 2
    video = "[cqt]"

    if preset.waveform and wave_png is not None:
        idx = next_idx
        next_idx += 1
        inputs += ["-loop", "1", "-i", str(wave_png)]
        parts.append(f"[{idx}:v]scale={w}:{WAVE_STRIP_H},format=yuva420p[wavebg]")
        parts.append(f"color=c=red:s=2x{WAVE_STRIP_H}:r={preset.fps}[ph]")
        parts.append(f"[wavebg][ph]overlay=x='t/{duration}*{w}':y=0:shortest=1[wave]")
        parts.append("[cqt][wave]vstack[stacked]")
        video = "[stacked]"

    if preset.overlays and cover_png is not None:
        idx = next_idx
        next_idx += 1
        inputs += ["-i", str(cover_png)]
        parts.append(f"[{idx}:v]scale=80:-1[logo]")
        parts.append(f"{video}[logo]overlay=x=10:y=10[withlogo]")
        video = "[withlogo]"

    if preset.overlays and title_file is not None:
        parts.append(
            f"{video}drawtext=textfile={title_file}:fontfile={FONT_SENTINEL}:"
            f"fontsize=28:fontcolor=white:x=100:y=15:"
            f"box=1:boxcolor=black@0.5:boxborderw=8,format=yuv420p[v]"
        )
    else:
        parts.append(f"{video}format=yuv420p[v]")

    return [
        *inputs,
        "-filter_complex",
        ";".join(parts),
        "-map",
        "[v]",
        "-map",
        "1:a",
        *_output_args(preset, out_path),
    ]


def build_still_args(
    preset: RenderPreset,
    *,
    cover_png: Path,
    bass_only: Path,
    out_path: Path,
) -> list[str]:
    w, h = preset.width, preset.height
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
    )
    return [
        "-loop",
        "1",
        "-i",
        str(cover_png),
        "-i",
        str(bass_only),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-tune",
        "stillimage",
        "-preset",
        preset.x264_preset,
        "-crf",
        str(preset.crf),
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "1",  # limited range → yuv420p not yuvj420p
        "-r",
        str(preset.fps),
        "-c:a",
        "copy",
        "-shortest",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
