"""Markdown-safe anchor detection, substitution, and link application."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import frontmatter
from markdown_it import MarkdownIt

if TYPE_CHECKING:
    from hugin.scanner import Post
    from hugin.hugo import HugoSite


def find_protected_zones(source: str) -> list[tuple[int, int]]:
    """Find byte ranges in source that must not be modified.

    Protected zones: fenced code blocks, inline code, headings,
    existing links, images, autolinks, HTML anchor tags.
    """
    zones = []

    # Fenced code blocks: ```...```
    for m in re.finditer(r"^```[^\n]*\n.*?^```", source, re.MULTILINE | re.DOTALL):
        zones.append((m.start(), m.end()))

    # Inline code: `...`
    for m in re.finditer(r"`[^`]+`", source):
        zones.append((m.start(), m.end()))

    # ATX headings: # ...
    for m in re.finditer(r"^#{1,6}\s+.*$", source, re.MULTILINE):
        zones.append((m.start(), m.end()))

    # Setext headings: underlined with === or ---
    for m in re.finditer(r"^.+\n[=\-]{2,}\s*$", source, re.MULTILINE):
        zones.append((m.start(), m.end()))

    # Existing Markdown links: [text](url)
    for m in re.finditer(r"\[([^\]]+)\]\([^)]+\)", source):
        zones.append((m.start(), m.end()))

    # Reference-style links: [text][ref]
    for m in re.finditer(r"\[([^\]]+)\]\[[^\]]*\]", source):
        zones.append((m.start(), m.end()))

    # Reference definitions: [ref]: url
    for m in re.finditer(r"^\[([^\]]+)\]:\s+\S+", source, re.MULTILINE):
        zones.append((m.start(), m.end()))

    # Images: ![alt](url)
    for m in re.finditer(r"!\[([^\]]*)\]\([^)]+\)", source):
        zones.append((m.start(), m.end()))

    # Autolinks: <https://...>
    for m in re.finditer(r"<https?://[^>]+>", source):
        zones.append((m.start(), m.end()))

    # HTML anchor tags: <a href="...">...</a>
    for m in re.finditer(r"<a\s[^>]*>.*?</a>", source, re.IGNORECASE | re.DOTALL):
        zones.append((m.start(), m.end()))

    # Sort and merge overlapping zones
    zones.sort()
    merged = []
    for start, end in zones:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    return merged


def is_in_protected_zone(pos: int, length: int, zones: list[tuple[int, int]]) -> bool:
    """Check if a text range overlaps any protected zone."""
    end = pos + length
    for z_start, z_end in zones:
        if pos < z_end and end > z_start:
            return True
    return False


def extract_existing_links(body: str) -> set[str]:
    """Extract all URLs from existing links in the post body."""
    urls = set()

    # Inline Markdown links: [text](url) — negative lookbehind to skip images ![](url)
    for m in re.finditer(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)", body):
        urls.add(m.group(2).strip())

    # Reference-style definitions: [ref]: url
    for m in re.finditer(r"^\[([^\]]+)\]:\s+(\S+)", body, re.MULTILINE):
        urls.add(m.group(2).strip())

    # Autolinks: <https://...>
    for m in re.finditer(r"<(https?://[^>]+)>", body):
        urls.add(m.group(1))

    # HTML anchors: <a href="url">
    for m in re.finditer(r'<a\s+[^>]*href=["\']([^"\']+)["\']', body, re.IGNORECASE):
        urls.add(m.group(1))

    return urls


def _split_paragraphs(body: str) -> list[tuple[int, int]]:
    """Split body into paragraph ranges (start, end) by blank lines."""
    paragraphs = []
    pos = 0
    for block in re.split(r"\n\n+", body):
        start = body.find(block, pos)
        if start != -1:
            paragraphs.append((start, start + len(block)))
            pos = start + len(block)
    return paragraphs


def _paragraph_has_link(body: str, para_start: int, para_end: int) -> bool:
    """Check if a paragraph already contains a link."""
    para_text = body[para_start:para_end]
    # Check for markdown links, autolinks, or HTML anchors
    if re.search(r"\[([^\]]+)\]\([^)]+\)", para_text):
        return True
    if re.search(r"<https?://[^>]+>", para_text):
        return True
    if re.search(r"<a\s", para_text, re.IGNORECASE):
        return True
    return False


def convert_html_links_to_markdown(body: str) -> str:
    """Convert safe single-line HTML <a> tags to Markdown links."""

    def replace_a_tag(match):
        full = match.group(0)
        # Only convert single-line, plain-text content
        if "\n" in full:
            return full
        # Preserve links with rel attributes (e.g. nofollow)
        if "rel=" in full.lower():
            return full
        href = re.search(r'href=["\']([^"\']+)["\']', full)
        text = re.search(r">(.*?)</a>", full, re.IGNORECASE)
        if href and text and "<" not in text.group(1):
            return f"[{text.group(1)}]({href.group(1)})"
        return full

    return re.sub(
        r"<a\s[^>]*>.*?</a>",
        replace_a_tag,
        body,
        flags=re.IGNORECASE,
    )


def _find_whole_word(body: str, anchor: str, start: int = 0) -> int:
    """Find anchor in body ensuring it doesn't start or end mid-word.

    Treats hyphens as word-joining characters to handle compound words
    like não-fumante, ex-presidente, etc.
    """
    pattern = r"(?<![\w\-])" + re.escape(anchor) + r"(?![\w\-])"
    m = re.search(pattern, body[start:])
    if m:
        return start + m.start()
    return -1


def find_keyword_anchors(
    body: str,
    candidates: list[dict],
) -> list[dict]:
    """Find deterministic anchors by matching candidate slug keywords in the body.

    For each candidate, extracts keywords from its URL slug and searches
    for them (case-insensitive) in the body. Returns the longest keyword
    match as anchor text, preserving the original case from the body.

    Args:
        body: the raw post body
        candidates: list of dicts with 'title' and 'url'

    Returns:
        List of dicts with 'anchor_text' and 'target_url'
    """
    zones = find_protected_zones(body)
    results = []

    for c in candidates:
        url = c["url"]
        slug = url.strip("/").rsplit("/", 1)[-1] if "/" in url else url
        keywords = [k for k in slug.split("-") if len(k) >= 4]
        # Try longest keywords first — more specific
        keywords.sort(key=len, reverse=True)

        for kw in keywords:
            pattern = r"(?<![\w\-])" + re.escape(kw) + r"(?![\w\-])"
            m = re.search(pattern, body, re.IGNORECASE)
            if m and not is_in_protected_zone(m.start(), len(kw), zones):
                anchor = body[m.start():m.end()]
                results.append({"anchor_text": anchor, "target_url": url})
                break

    return results


def check_anchor_viable(
    body: str,
    anchor: str,
    max_per_paragraph: int = 1,
) -> bool:
    """Check if an anchor can actually be placed in the body."""
    pos = _find_whole_word(body, anchor)
    if pos == -1:
        return False

    zones = find_protected_zones(body)
    if is_in_protected_zone(pos, len(anchor), zones):
        return False

    paragraphs = _split_paragraphs(body)
    for p_start, p_end in paragraphs:
        if p_start <= pos < p_end:
            # Count existing links in this paragraph
            link_count = 0
            if _paragraph_has_link(body, p_start, p_end):
                link_count = 1
            if link_count >= max_per_paragraph:
                return False
            break

    return True


def _is_nofollow_url(url: str) -> bool:
    """Check if a URL needs rel=nofollow (affiliate links)."""
    lower = url.lower()
    return "amazon" in lower or "amzn" in lower


def apply_links(
    body: str,
    suggestions: list[dict],
    max_per_paragraph: int = 1,
) -> tuple[str, list[str]]:
    """Apply link substitutions to the body text.

    Args:
        body: the raw post body (no frontmatter)
        suggestions: list of dicts with 'anchor_text' and 'target_url'
        max_per_paragraph: max links per paragraph

    Returns:
        (modified_body, list of skipped anchor texts)
    """
    # Convert HTML links first
    body = convert_html_links_to_markdown(body)

    zones = find_protected_zones(body)
    paragraphs = _split_paragraphs(body)
    links_per_para = {i: 0 for i in range(len(paragraphs))}
    consumed_ranges = []
    skipped = []

    # Count existing links per paragraph
    for i, (p_start, p_end) in enumerate(paragraphs):
        if _paragraph_has_link(body, p_start, p_end):
            links_per_para[i] += 1

    for suggestion in suggestions:
        anchor = suggestion["anchor_text"]
        target = suggestion["target_url"]
        if _is_nofollow_url(target):
            replacement = f'<a href="{target}" rel="nofollow">{anchor}</a>'
        else:
            replacement = f"[{anchor}]({target})"

        # Find first valid occurrence
        search_start = 0
        placed = False

        while True:
            pos = _find_whole_word(body, anchor, search_start)
            if pos == -1:
                break

            anchor_end = pos + len(anchor)

            # Check protected zone
            if is_in_protected_zone(pos, len(anchor), zones):
                search_start = anchor_end
                continue

            # Check consumed by prior substitution
            if any(pos < ce and anchor_end > cs for cs, ce in consumed_ranges):
                search_start = anchor_end
                continue

            # Find which paragraph this is in
            para_idx = None
            for i, (p_start, p_end) in enumerate(paragraphs):
                if p_start <= pos < p_end:
                    para_idx = i
                    break

            if para_idx is None:
                search_start = anchor_end
                continue

            # Check paragraph link limit (0 = unlimited)
            if max_per_paragraph and links_per_para[para_idx] >= max_per_paragraph:
                search_start = anchor_end
                continue

            # Apply substitution
            body = body[:pos] + replacement + body[anchor_end:]

            # Update tracking
            consumed_ranges.append((pos, pos + len(replacement)))
            links_per_para[para_idx] += 1

            # Recalculate zones and paragraphs after substitution
            zones = find_protected_zones(body)
            paragraphs = _split_paragraphs(body)

            placed = True
            break

        if not placed:
            skipped.append(anchor)

    return body, skipped


def list_links(body: str) -> list[dict]:
    """List all links with anchor text, URL, and position.

    Returns list of dicts with keys: anchor_text, url, pos.
    """
    results = []
    for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", body):
        results.append({
            "anchor_text": m.group(1),
            "url": m.group(2),
            "pos": m.start(),
        })
    return results


def remove_specific_links(body: str, urls_to_remove: set[str]) -> tuple[str, int]:
    """Remove specific links by URL, keeping anchor text.

    Returns (modified_body, count_removed).
    """
    count = 0

    def replace_if_selected(match):
        nonlocal count
        text = match.group(1)
        url = match.group(2)
        if url in urls_to_remove:
            count += 1
            return text
        return match.group(0)

    body = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace_if_selected, body)
    return body, count


def strip_internal_links(body: str) -> tuple[str, int]:
    """Remove all internal links (starting with /) from the body, keeping the anchor text.

    Returns (modified_body, count_of_links_removed).
    """
    count = 0

    def replace_internal(match):
        nonlocal count
        text = match.group(1)
        url = match.group(2)
        if url.startswith("/"):
            count += 1
            return text
        return match.group(0)

    body = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace_internal, body)
    return body, count


def count_internal_links(body: str) -> int:
    """Count internal links (URLs starting with /) in the body."""
    return sum(1 for m in re.finditer(r"\[([^\]]+)\]\((/[^)]+)\)", body))


@dataclass
class BrokenLink:
    """A link in a post that points to a draft or non-existent post."""

    source_post: Post
    anchor_text: str
    target_url: str
    reason: str  # "draft" or "not_found"


def find_broken_links(all_posts: list[Post], site: HugoSite) -> list[BrokenLink]:
    """Find all internal links that point to draft or non-existent posts.

    Builds a URL→Post map, then checks every internal link in every post.
    External links (not starting with /) are ignored.
    """
    # Build URL → Post map
    url_to_post: dict[str, Post] = {}
    for post in all_posts:
        url = site.post_url(post.metadata, post.filename)
        url_to_post[url] = post

    # Scan all posts for broken internal links
    broken: list[BrokenLink] = []
    for post in all_posts:
        links = list_links(post.content)
        for link in links:
            url = link["url"]
            # Only check internal links
            if not url.startswith("/"):
                continue

            target = url_to_post.get(url)
            if target is None:
                broken.append(BrokenLink(
                    source_post=post,
                    anchor_text=link["anchor_text"],
                    target_url=url,
                    reason="not_found",
                ))
            elif target.metadata.get("draft"):
                broken.append(BrokenLink(
                    source_post=post,
                    anchor_text=link["anchor_text"],
                    target_url=url,
                    reason="draft",
                ))

    return broken


def write_post_with_links(
    path: Path,
    body: str,
) -> None:
    """Write post with updated body. Delegates to writer.save_post."""
    from hugin.writer import write_body

    write_body(path, body)
