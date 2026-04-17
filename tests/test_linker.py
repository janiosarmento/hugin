"""Tests for munin/linker.py — protected zones, substitution, link application."""

import pytest
from pathlib import Path

from hugin.linker import (
    apply_links,
    convert_html_links_to_markdown,
    extract_existing_links,
    find_protected_zones,
    is_in_protected_zone,
    write_post_with_links,
)


class TestProtectedZones:
    def test_fenced_code_block(self):
        src = "text\n```\nsystemd timers\n```\nmore text"
        zones = find_protected_zones(src)
        pos = src.find("systemd timers")
        assert is_in_protected_zone(pos, len("systemd timers"), zones)

    def test_inline_code(self):
        src = "use `systemd timers` for scheduling"
        zones = find_protected_zones(src)
        pos = src.find("systemd timers")
        assert is_in_protected_zone(pos, len("systemd timers"), zones)

    def test_heading(self):
        src = "# Systemd Timers\n\nSome content about systemd timers"
        zones = find_protected_zones(src)
        # "Systemd Timers" in heading is protected
        assert is_in_protected_zone(2, len("Systemd Timers"), zones)
        # "systemd timers" in body is NOT protected
        body_pos = src.find("systemd timers")
        assert not is_in_protected_zone(body_pos, len("systemd timers"), zones)

    def test_existing_markdown_link(self):
        src = "check [systemd timers](/posts/st/) for more"
        zones = find_protected_zones(src)
        pos = src.find("systemd timers")
        assert is_in_protected_zone(pos, len("systemd timers"), zones)

    def test_image_is_protected(self):
        src = "![systemd timers](/img/st.png) here"
        zones = find_protected_zones(src)
        pos = src.find("systemd timers")
        assert is_in_protected_zone(pos, len("systemd timers"), zones)

    def test_html_anchor(self):
        src = '<a href="/posts/st/">systemd timers</a> are useful'
        zones = find_protected_zones(src)
        pos = src.find("systemd timers")
        assert is_in_protected_zone(pos, len("systemd timers"), zones)

    def test_autolink(self):
        src = "visit <https://example.com/systemd-timers> now"
        zones = find_protected_zones(src)
        pos = src.find("https://example.com")
        assert is_in_protected_zone(pos, len("https://example.com"), zones)

    def test_plain_text_not_protected(self):
        src = "I really like systemd timers for scheduling tasks"
        zones = find_protected_zones(src)
        pos = src.find("systemd timers")
        assert not is_in_protected_zone(pos, len("systemd timers"), zones)


class TestExtractExistingLinks:
    def test_markdown_links(self):
        body = "check [timers](/posts/st/) and [cron](/posts/cron/)"
        urls = extract_existing_links(body)
        assert "/posts/st/" in urls
        assert "/posts/cron/" in urls

    def test_reference_links(self):
        body = "see [timers][1]\n\n[1]: /posts/st/"
        urls = extract_existing_links(body)
        assert "/posts/st/" in urls

    def test_autolinks(self):
        body = "visit <https://example.com/post>"
        urls = extract_existing_links(body)
        assert "https://example.com/post" in urls

    def test_html_anchors(self):
        body = '<a href="/posts/st/">timers</a>'
        urls = extract_existing_links(body)
        assert "/posts/st/" in urls

    def test_images_excluded(self):
        body = "![alt](/img/photo.png) and [link](/posts/st/)"
        urls = extract_existing_links(body)
        assert "/posts/st/" in urls
        assert "/img/photo.png" not in urls


class TestConvertHtmlLinks:
    def test_simple_conversion(self):
        body = '<a href="/posts/st/">systemd timers</a>'
        result = convert_html_links_to_markdown(body)
        assert result == "[systemd timers](/posts/st/)"

    def test_multiline_not_converted(self):
        body = '<a href="/posts/st/">\nsystemd timers\n</a>'
        result = convert_html_links_to_markdown(body)
        assert "<a" in result  # Not converted

    def test_nested_html_not_converted(self):
        body = '<a href="/posts/st/"><strong>timers</strong></a>'
        result = convert_html_links_to_markdown(body)
        assert "<a" in result  # Not converted due to nested HTML


class TestApplyLinks:
    def test_basic_substitution(self):
        body = "I use systemd timers for scheduling.\n\nAnother paragraph."
        suggestions = [{"anchor_text": "systemd timers", "target_url": "/posts/st/"}]
        result, skipped = apply_links(body, suggestions)
        assert "[systemd timers](/posts/st/)" in result
        assert skipped == []

    def test_protected_zone_skipped(self):
        body = "```\nsystemd timers\n```\n\nI use systemd timers too."
        suggestions = [{"anchor_text": "systemd timers", "target_url": "/posts/st/"}]
        result, skipped = apply_links(body, suggestions)
        # Should link the one in the paragraph, not the code block
        assert "```\nsystemd timers\n```" in result
        assert "[systemd timers](/posts/st/)" in result

    def test_max_per_paragraph(self):
        body = "I like docker and docker-compose for containers."
        suggestions = [
            {"anchor_text": "docker", "target_url": "/posts/docker/"},
            {"anchor_text": "docker-compose", "target_url": "/posts/dc/"},
        ]
        result, skipped = apply_links(body, suggestions, max_per_paragraph=1)
        # Only one link per paragraph
        assert result.count("](/") == 1

    def test_anchor_not_found(self):
        body = "This post is about Linux."
        suggestions = [{"anchor_text": "systemd timers", "target_url": "/posts/st/"}]
        result, skipped = apply_links(body, suggestions)
        assert result == body
        assert "systemd timers" in skipped

    def test_first_occurrence_wins(self):
        body = "I use systemd timers daily. Yes, systemd timers are great."
        suggestions = [{"anchor_text": "systemd timers", "target_url": "/posts/st/"}]
        result, skipped = apply_links(body, suggestions)
        # First occurrence is linked, second is not
        first_link = result.find("[systemd timers]")
        second_plain = result.find("systemd timers", first_link + 20)
        assert first_link != -1
        # Second occurrence should still be plain (not linked)
        assert second_plain == -1 or result[second_plain - 1] != "["

    def test_case_sensitive(self):
        body = "Systemd Timers are different from systemd timers."
        suggestions = [{"anchor_text": "systemd timers", "target_url": "/posts/st/"}]
        result, skipped = apply_links(body, suggestions)
        assert "[systemd timers](/posts/st/)" in result
        assert "Systemd Timers" in result  # Uppercase preserved, not linked

    def test_html_a_converted_before_linking(self):
        body = '<a href="/posts/old/">old link</a>\n\nI like systemd timers.'
        suggestions = [{"anchor_text": "systemd timers", "target_url": "/posts/st/"}]
        result, skipped = apply_links(body, suggestions)
        assert "[old link](/posts/old/)" in result
        assert "[systemd timers](/posts/st/)" in result


class TestWritePostWithLinks:
    def test_atomic_write(self, tmp_path):
        post_path = tmp_path / "test.md"
        post_path.write_text(
            "---\ntitle: Test\ndate: 2026-01-01\n---\n\nOriginal content.\n"
        )

        write_post_with_links(post_path, "Modified content with [link](/url/).")

        result = post_path.read_text()
        assert "Modified content with [link](/url/)" in result
        assert "lastmod:" in result
        assert "title: Test" in result
