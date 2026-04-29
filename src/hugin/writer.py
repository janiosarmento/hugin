"""Centralised post writing — single path for all frontmatter serialisation."""

import os
import tempfile
from datetime import date, datetime
from pathlib import Path

import frontmatter
import yaml
from frontmatter.default_handlers import SafeDumper as _FMDumper


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
    """Ensure description is penultimate and tags is last."""
    ordered = {}
    desc = meta.pop("description", None)
    tags = meta.pop("tags", None)

    for key, value in meta.items():
        ordered[key] = value

    if desc is not None:
        ordered["description"] = desc
    if tags is not None:
        ordered["tags"] = tags

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

    dir_path = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".md")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(frontmatter.dumps(post, sort_keys=False))
            f.write("\n")
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def save_raw(path: Path, text: str) -> None:
    """Write raw file content as-is (no frontmatter processing). Atomic."""
    dir_path = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".md")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def write_tags(path: Path, tags: list[str]) -> None:
    """Write the full tag list to a post, replacing any existing tags."""
    post = frontmatter.load(str(path))
    post.metadata["tags"] = tags
    save_post(path, post)


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
