"""Centralised post writing — single path for all frontmatter serialisation."""

import re
from datetime import date, datetime
from pathlib import Path

import frontmatter
import yaml
from frontmatter.default_handlers import SafeDumper as _FMDumper

from hugin.fsutil import atomic_write_text


# --- Custom YAML representer for datetime/date ---
# PyYAML's default datetime representer uses a space separator
# (2011-11-22 12:28:43) instead of ISO 8601 T (2011-11-22T12:28:43).
# Pages CMS expects the T separator, unquoted.
#
# Register on both the frontmatter dumper (CSafeDumper) and the
# standard SafeDumper so all YAML output is consistent.

def _represent_datetime(dumper, data):
    return dumper.represent_scalar(
        "tag:yaml.org,2002:timestamp", data.isoformat(timespec="seconds"),
    )


def _represent_date(dumper, data):
    return dumper.represent_scalar(
        "tag:yaml.org,2002:timestamp", data.isoformat(),
    )


for _dumper in (yaml.SafeDumper, _FMDumper):
    _dumper.add_representer(datetime, _represent_datetime)
    _dumper.add_representer(date, _represent_date)


# Date-like fields that should always be datetime objects for proper
# YAML serialisation (unquoted timestamp with T separator).
_DATE_FIELDS = frozenset({"date", "lastmod", "publishDate", "expiryDate"})


def _ensure_datetime_fields(meta: dict) -> None:
    """Convert date-like string values back to datetime objects in-place.

    When a post is loaded from YAML with quoted dates ('2011-11-22T12:28:43'),
    PyYAML returns strings instead of datetime objects. This function converts
    them back so the custom representer can format them correctly (unquoted, T).
    """
    for key in _DATE_FIELDS:
        value = meta.get(key)
        if isinstance(value, str) and value:
            try:
                meta[key] = datetime.fromisoformat(value)
            except ValueError:
                pass


def _reorder_metadata(meta: dict) -> dict:
    """Ensure description, tags, then keywords come last, in that order."""
    ordered = {}
    desc = meta.pop("description", None)
    tags = meta.pop("tags", None)
    keywords = meta.pop("keywords", None)

    for key, value in meta.items():
        ordered[key] = value

    if desc is not None:
        ordered["description"] = desc
    if tags is not None:
        ordered["tags"] = tags
    if keywords is not None:
        ordered["keywords"] = keywords

    return ordered


def save_post(path: Path, post) -> None:
    """Write a frontmatter Post to disk.

    This is the ONLY function that should write post files.
    It handles: lastmod update, datetime stringification,
    metadata reordering, and atomic write (temp file + rename).
    """
    post.metadata["lastmod"] = datetime.now()
    _ensure_datetime_fields(post.metadata)
    post.metadata = _reorder_metadata(dict(post.metadata))

    atomic_write_text(path, frontmatter.dumps(post, sort_keys=False) + "\n")


def save_raw(path: Path, text: str) -> None:
    """Write raw file content as-is (no frontmatter processing). Atomic."""
    atomic_write_text(path, text if text.endswith("\n") else text + "\n")


def create_post(
    path: Path,
    *,
    title: str,
    slug: str,
    category: str | None = None,
    body: str = "",
    prompt: str | None = None,
    refactor_of: str | None = None,
    thumbnail: str | None = None,
    translation_key: str | None = None,
    author: str | None = None,
    author_field: str = "author",
) -> frontmatter.Post:
    """Create a new post with a complete frontmatter skeleton.

    Fields the caller can't determine yet (translationKey, thumbnail,
    description, categories/tags without a known value) are filled with
    "TBD" placeholders rather than left out, so PagesCMS's schema
    validation doesn't fail on missing required fields.

    `prompt` (LLM-generated posts) is kept in the frontmatter for reference.
    `refactor_of` is the filename of the post this one rewrites.
    `author` is the post's author (multi-author blogs).
    """
    metadata = {
        "title": title,
        "slug": slug,
        "translationKey": translation_key or "TBD",
        "date": datetime.now(),
        "thumbnail": thumbnail or "TBD",
        "categories": [category] if category else ["TBD"],
        "toc": False,
        "draft": True,
        "description": "TBD",
        "tags": ["TBD"],
    }
    if author:
        metadata[author_field] = [author] if author_field == "authors" else author
    if prompt:
        metadata["prompt"] = prompt
    if refactor_of:
        metadata["refactor_of"] = refactor_of
    post = frontmatter.Post(body, **metadata)
    save_post(path, post)
    return post


def write_tags(path: Path, tags: list[str]) -> None:
    """Write the full tag list to a post, replacing any existing tags."""
    post = frontmatter.load(str(path))
    post.metadata["tags"] = tags
    save_post(path, post)


_FRONTMATTER_END = re.compile(r"^-{3,}\s*$")
_KEYWORDS_KEY = re.compile(r"^keywords\s*:")


def write_keywords_block(path: Path, keywords: list[str]) -> None:
    """Set a post's `keywords` to exactly `keywords`, touching nothing else in the file.

    Not a full re-save: that would also re-quote and re-wrap the other fields.
    An existing `keywords` entry is replaced where it stands (block or flow style);
    without one, the entry goes at the end of the frontmatter.
    """
    lines = path.read_text(encoding="utf-8").split("\n")
    block = yaml.safe_dump(
        {"keywords": keywords}, allow_unicode=True, sort_keys=False, width=float("inf"),
    ).splitlines()
    if not _FRONTMATTER_END.match(lines[0]):
        # No frontmatter yet: the block becomes it
        atomic_write_text(path, "\n".join(["---", *block, "---", *lines]))
        return
    end = next(i for i in range(1, len(lines)) if _FRONTMATTER_END.match(lines[i]))
    start = next((i for i in range(1, end) if _KEYWORDS_KEY.match(lines[i])), None)
    if start is None:
        lines[end:end] = block
    else:
        stop = start + 1
        while stop < end and (lines[stop].startswith("-") or lines[stop][:1].isspace()):
            stop += 1
        lines[start:stop] = block
    atomic_write_text(path, "\n".join(lines))


def write_summary(path: Path, summary: str) -> None:
    """Write a new description to a post."""
    post = frontmatter.load(str(path))
    post.metadata["description"] = summary
    save_post(path, post)


def write_body(path: Path, body: str) -> None:
    """Write updated body content to a post."""
    post = frontmatter.load(str(path))
    post.content = body
    save_post(path, post)
