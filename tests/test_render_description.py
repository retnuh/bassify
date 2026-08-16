from __future__ import annotations

from bassify.render.description import DEFAULT_TEMPLATE, clean_title, render_description
from bassify.render.metadata import TrackMeta


def test_clean_title_strips_bass_only_suffix():
    assert clean_title("The Thrill Is Gone (Bass Only)") == "The Thrill Is Gone"


def test_clean_title_is_case_insensitive():
    assert clean_title("Foo (bass only)") == "Foo"
    assert clean_title("Foo (BASS ONLY)") == "Foo"


def test_clean_title_no_suffix_unchanged():
    assert clean_title("Turnarounds") == "Turnarounds"


def test_clean_title_none_and_empty():
    assert clean_title(None) is None
    assert clean_title("") == ""


def test_render_description_includes_key_and_bpm_when_present():
    meta = TrackMeta("40", "The Thrill Is Gone (Bass Only)", "Ed Friedland", bpm=89.1)
    text = render_description(DEFAULT_TEMPLATE, meta, key_root_pc=11, videos=None)

    assert "40: The Thrill Is Gone" in text
    assert "(Bass Only)" not in text  # clean_title applied before substitution
    assert "Key: B" in text
    assert "Tempo: 89 BPM" in text


def test_render_description_omits_key_and_bpm_when_absent():
    meta = TrackMeta("03", "Turnarounds", "Ed Friedland")  # no bpm
    text = render_description(DEFAULT_TEMPLATE, meta, key_root_pc=None, videos=None)

    assert "Key:" not in text
    assert "Tempo:" not in text
    assert "BPM" not in text


def test_render_description_videos_block_lists_entries_with_titles():
    meta = TrackMeta("36", "Born Under A Bad Sign", "Ed Friedland")
    videos = [
        {"title": "Albert King - Born Under A Bad Sign", "url": "https://youtube.com/watch?v=x"},
        {"title": "Albert King (Official Audio)", "url": "https://youtube.com/watch?v=y"},
    ]
    text = render_description(DEFAULT_TEMPLATE, meta, key_root_pc=None, videos=videos)

    assert "Original recording(s) this is based on:" in text
    assert "- Albert King - Born Under A Bad Sign: https://youtube.com/watch?v=x" in text
    assert "- Albert King (Official Audio): https://youtube.com/watch?v=y" in text


def test_render_description_video_without_title_gets_a_fallback_label():
    meta = TrackMeta("36", "Born Under A Bad Sign", "Ed Friedland")
    videos = [{"url": "https://youtube.com/watch?v=x"}]
    text = render_description(DEFAULT_TEMPLATE, meta, key_root_pc=None, videos=videos)
    assert "- Original recording: https://youtube.com/watch?v=x" in text


def test_render_description_no_videos_block_when_empty_or_none():
    meta = TrackMeta("01", "Twelve Bar Blues Form", "Ed Friedland")
    text_none = render_description(DEFAULT_TEMPLATE, meta, key_root_pc=None, videos=None)
    text_empty = render_description(DEFAULT_TEMPLATE, meta, key_root_pc=None, videos=[])
    assert "Original recording(s)" not in text_none
    assert "Original recording(s)" not in text_empty


def test_render_description_video_missing_url_is_skipped():
    meta = TrackMeta("01", "Foo", "Ed Friedland")
    videos = [{"title": "No URL here"}]
    text = render_description(DEFAULT_TEMPLATE, meta, key_root_pc=None, videos=videos)
    assert "Original recording(s)" not in text


def test_render_description_custom_template_uses_placeholders():
    # ${bpm_line}Book, not $bpm_lineBook: string.Template greedily consumes
    # identifier characters, so an un-braced placeholder directly followed by
    # more letters merges into one (unmatched) name -- braces disambiguate,
    # same footgun a hand-written yaml template could hit.
    meta = TrackMeta("07", "Uptown Up", "Ed Friedland", bpm=100.0)
    template = "Track $number - $name by $artist. $key_line${bpm_line}Book: inline text."
    text = render_description(template, meta, key_root_pc=7, videos=None)

    assert text == (
        "Track 07 - Uptown Up by Ed Friedland. Key: G\nTempo: 100 BPM\nBook: inline text."
    )


def test_render_description_unknown_placeholder_left_literal_not_a_crash():
    """string.Template.safe_substitute leaves unrecognised $placeholders as-is
    instead of raising -- a typo'd field name in a hand-edited yaml template
    must not crash the whole batch."""
    meta = TrackMeta("01", "Foo", "Ed Friedland")
    text = render_description("Hello $typo_field", meta, key_root_pc=None, videos=None)
    assert text == "Hello $typo_field"


def test_render_description_no_number_falls_back_to_bare_name():
    meta = TrackMeta(None, "Foo", None)
    text = render_description("$title_line", meta, key_root_pc=None, videos=None)
    assert text == "Foo"


def test_render_description_missing_name_uses_untitled():
    meta = TrackMeta("01", None, None)
    text = render_description("$title_line", meta, key_root_pc=None, videos=None)
    assert text == "01: Untitled"


def test_render_description_default_template_credits_the_project():
    meta = TrackMeta("01", "Foo", "Ed Friedland")
    text = render_description(DEFAULT_TEMPLATE, meta, key_root_pc=None, videos=None)
    assert "github.com/retnuh/bassify" in text


def test_render_description_project_url_is_overridable():
    meta = TrackMeta("01", "Foo", "Ed Friedland")
    text = render_description(
        "$project_url", meta, key_root_pc=None, videos=None, project_url="https://example.com/fork"
    )
    assert text == "https://example.com/fork"
