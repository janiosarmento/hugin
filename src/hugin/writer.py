"""Injeção de tags no frontmatter dos posts."""

from datetime import datetime
from pathlib import Path

import frontmatter


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


def _save_post(path: Path, post) -> None:
    """Write post back to file, updating lastmod."""
    post.metadata["lastmod"] = datetime.now().isoformat(timespec="seconds")

    post.metadata = _reorder_metadata(dict(post.metadata))

    with open(path, "w") as f:
        f.write(frontmatter.dumps(post, sort_keys=False))
        f.write("\n")


def write_tags(path: Path, tags: list[str]) -> None:
    """Write the full tag list to a post, replacing any existing tags."""
    post = frontmatter.load(str(path))
    post.metadata["tags"] = tags
    _save_post(path, post)


def write_summary(path: Path, summary: str) -> None:
    """Write a new description to a post."""
    post = frontmatter.load(str(path))
    post.metadata["description"] = summary
    _save_post(path, post)
