"""[img]…[/img] placeholders: where an illustration could go, described for an image generator.

The draft prompts ask the LLM to mark spots for an optional illustration with
`[img]description[/img]`, the description written in English as a prompt for
ChatGPT or Midjourney. The author generates the image by hand and puts it in
place of the placeholder. Hugo would publish a forgotten placeholder as plain
text, so a published post with `[img]` blocks the sync; a draft may keep them.

Placeholders inside code (fenced blocks or inline code spans) are text about
placeholders, not placeholders, and are left alone.
"""

from __future__ import annotations

import re
from pathlib import Path

from hugin.drafts_only import is_draft

_BODY = r"(?:(?!\[/?img\]).)*?"  # never runs past another marker
# A placeholder alone on its line, with the blank line after it
_LINE = re.compile(rf"^[ \t]*\[img\]{_BODY}\[/img\][ \t]*(?:\n[ \t]*(?=\n)\n?|\n|\Z)", re.I | re.S | re.M)
_INLINE = re.compile(rf"[ \t]?\[img\]({_BODY})\[/img\]", re.I | re.S)
_ANY = re.compile(rf"\[img\]({_BODY})\[/img\]", re.I | re.S)
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?(?:^[ \t]*\1[ \t]*$|\Z)", re.S | re.M)
# Inline code never runs past a blank line
_CODE_SPAN = re.compile(r"(`+)(?!`)(?:(?!\n[ \t]*\n).)+?(?<!`)\1(?!`)", re.S)


def _code_spans(text: str) -> list[tuple[int, int]]:
    spans = [m.span() for m in _FENCE.finditer(text)]
    for m in _CODE_SPAN.finditer(text):
        if not any(a <= m.start() < b for a, b in spans):
            spans.append(m.span())
    return spans


def _outside_code(text: str, pattern: re.Pattern) -> list[re.Match]:
    code = _code_spans(text)
    return [m for m in pattern.finditer(text) if not any(a <= m.start() < b for a, b in code)]


def descriptions(text: str) -> list[str]:
    """The descriptions of the placeholders in `text`, in order, on one line each."""
    return [d for d in (" ".join(m.group(1).split()) for m in _outside_code(text, _ANY)) if d]


def remove(text: str) -> str:
    """`text` without its placeholders; one alone on a line takes the line with it."""
    original = text
    for pattern in (_LINE, _INLINE):
        for m in reversed(_outside_code(text, pattern)):
            text = text[: m.start()] + text[m.end():]
    if text != original and text.endswith("\n\n") and not original.endswith("\n\n"):
        text = text.rstrip("\n") + "\n"  # one at the end took its line, not the blank before it
    return text


def published_with_marker(content_dir: Path) -> list[Path]:
    """Posts under `content_dir` that are not drafts and contain `[img]` anywhere.

    Stricter than `descriptions`: a marker inside code counts too, so a post
    that teaches the shortcode is caught as well. Such posts block a sync.
    """
    found = []
    for path in sorted(content_dir.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "[img]" in text.lower() and not is_draft(text):
            found.append(path)
    return found
