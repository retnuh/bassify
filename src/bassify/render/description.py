from __future__ import annotations

import re
from string import Template

from bassify.render.labels import note_name
from bassify.render.metadata import TrackMeta

BOOK_URL = (
    "https://www.halleonard.com/product-family/PC790/"
    "blues-bass-a-guide-to-the-essential-styles-and-techniques"
)
PROJECT_URL = "https://github.com/retnuh/bassify"

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
Book: $book_url
$videos_block
Generated with bassify: $project_url

#bassguitar #basscover #bluesbass #basslesson
"""

_BASS_ONLY_SUFFIX = re.compile(r"\s*\(bass only\)\s*$", re.IGNORECASE)


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
    book_url: str = BOOK_URL,
    project_url: str = PROJECT_URL,
) -> str:
    """Fill a stdlib Template blurb from track metadata, for a
    <track>_youtube_description.txt sidecar."""
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
        book_url=book_url,
        project_url=project_url,
    )
