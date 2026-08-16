from __future__ import annotations

import re
import subprocess
from string import Template

from bassify.render.labels import note_name
from bassify.render.metadata import TrackMeta

# stdlib string.Template has no conditionals -- optional fields (key, bpm,
# videos) are pre-formatted into whole lines/blocks in render_description()
# and substituted as empty strings when the data is missing, rather than
# living as bare $placeholders a missing value would leave dangling.
DEFAULT_TEMPLATE = """\
$title_line

Bass-only practice track from Hal Leonard's "Blues Bass: A Guide to the \
Essential Styles and Techniques" by Ed Friedland -- isolated bass line, \
with the original count-in and narration left intact so it's still \
practice-along ready.
$key_line$bpm_line
$videos_block
Generated with bassify: $project_url

#bassguitar #basscover #bluesbass #basslesson
"""

_BASS_ONLY_SUFFIX = re.compile(r"\s*\(bass only\)\s*$", re.IGNORECASE)
_SCP_STYLE = re.compile(r"^git@([^:]+):(.+)$")


def _detect_project_url() -> str:
    """Derive a browsable project URL from `git remote get-url origin`,
    rather than hardcoding this fork's URL as a constant."""
    try:
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return ""

    scp_match = _SCP_STYLE.match(remote)
    if scp_match:
        host, path = scp_match.groups()
        remote = f"https://{host}/{path}"
    if remote.endswith(".git"):
        remote = remote[: -len(".git")]
    return remote


def clean_title(name: str | None) -> str | None:
    """Strip a trailing '(Bass Only)' tag suffix.

    That suffix is correct context on the title card and the in-video
    overlay (it's genuinely a bass-only mix), but reads as an odd fragment
    baked into a paragraph of prose.
    """
    if not name:
        return name
    return _BASS_ONLY_SUFFIX.sub("", name).strip()


def render_description(
    template: str,
    meta: TrackMeta,
    key_root_pc: int | None,
    videos: list[dict] | None = None,
    project_url: str | None = None,
) -> str:
    """Fill a stdlib Template blurb from track metadata, for a
    <track>_youtube_description.txt sidecar."""
    if project_url is None:
        project_url = _detect_project_url()

    name = clean_title(meta.name) or "Untitled"
    title_line = f"{meta.number}: {name}" if meta.number else name

    key_line = f"Key: {note_name(key_root_pc)}\n" if key_root_pc is not None else ""
    bpm_line = f"Tempo: {round(meta.bpm)} BPM\n" if meta.bpm is not None else ""

    videos_block = ""
    if videos:
        lines = "\n".join(
            f"- {v.get('title') or 'Original recording'}: {v['url']}"
            for v in videos
            if v.get("url")
        )
        if lines:
            videos_block = f"\nOriginal recording(s) this is based on:\n{lines}\n"

    return Template(template).safe_substitute(
        number=meta.number or "",
        name=name,
        title_line=title_line,
        artist=meta.artist or "",
        key_line=key_line,
        bpm_line=bpm_line,
        videos_block=videos_block,
        project_url=project_url,
    )
