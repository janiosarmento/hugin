"""What only drafts carry: front matter fields taken out once a post is published.

The `prompt` field keeps the request an LLM draft was written from; it is of
no use once the post is out. git sync removes it from every post that is not
a draft. The field is cut from the text, so the rest of the front matter
keeps its formatting.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from hugin.fsutil import atomic_write_text

DRAFT_ONLY_FIELDS = ("prompt",)


def is_draft(text: str) -> bool:
    """True when the post's front matter says draft (true, "true", yes…)."""
    import frontmatter

    try:
        draft = frontmatter.loads(text).metadata.get("draft", False)
    except Exception:  # unparsable front matter: treat as published, the safe side
        return False
    if isinstance(draft, str):
        return draft.strip().lower() in ("true", "yes", "on", "1")
    return bool(draft)


def _front_matter(lines: list[str]) -> int | None:
    """Index of the line closing the YAML front matter, or None."""
    if not lines or lines[0].rstrip() != "---":
        return None
    return next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)


def remove_field(text: str, key: str) -> str:
    """`text` without the top-level `key` in its YAML front matter; unchanged when absent.

    The entry runs from its key to the next line starting at column 0 (its
    value may span indented and blank lines). The text is returned unchanged
    when the cut would change any other field.
    """
    lines = text.split("\n")
    end = _front_matter(lines)
    if end is None:
        return text
    start = next((i for i in range(1, end) if re.match(rf"{re.escape(key)}\s*:", lines[i])), None)
    if start is None:
        return text
    stop = start + 1
    while stop < end and (not lines[stop].strip() or lines[stop][:1].isspace()):
        stop += 1
    kept = lines[:start] + lines[stop:]
    try:
        before = yaml.safe_load("\n".join(lines[1:end])) or {}
        after = yaml.safe_load("\n".join(kept[1:end - (stop - start)])) or {}
    except yaml.YAMLError:
        return text
    before.pop(key, None)
    return "\n".join(kept) if before == after else text


def published_with_fields(content_dir: Path, keys=DRAFT_ONLY_FIELDS) -> list[Path]:
    """Posts under `content_dir` that are not drafts and have any of `keys` in their front matter."""
    found = []
    for path in sorted(content_dir.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(f"\n{key}:" in text for key in keys) and not is_draft(text):
            if any(remove_field(text, key) != text for key in keys):
                found.append(path)
    return found


def strip_fields(path: Path, keys=DRAFT_ONLY_FIELDS) -> bool:
    """Remove `keys` from the post's front matter; whether it changed."""
    text = path.read_text(encoding="utf-8")
    clean = text
    for key in keys:
        clean = remove_field(clean, key)
    if clean == text:
        return False
    atomic_write_text(path, clean)
    return True
