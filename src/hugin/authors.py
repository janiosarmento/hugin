"""Authors of a multi-author blog: who a post is by, the author list, and the default.

A post's author is its `author` front matter field. The author list puts the
authors that have a profile first, in the order of hugin/config.toml, then
any author that only appears on posts. The first one is the default for
posts that have no author.
"""

import json
from pathlib import Path

from hugin.fsutil import atomic_write_text
from hugin.project import AuthorsSettings
from hugin.scanner import Post


def post_author(post: Post) -> str:
    """The post's `author` front matter field, or "" when it has none."""
    return str(post.metadata.get("author") or "").strip()


def authors_of(posts: list[Post]) -> list[str]:
    """Distinct authors across the posts, in order of first appearance."""
    return list(dict.fromkeys(a for a in (post_author(p) for p in posts) if a))


def ordered_authors(settings: AuthorsSettings, posts: list[Post]) -> list[str]:
    """The configured list, then any author that only appears on the posts."""
    return list(dict.fromkeys([*settings.names, *authors_of(posts)]))


def default_author(settings: AuthorsSettings, posts: list[Post]) -> str | None:
    """The first author of the list: the author of posts that have none."""
    return next(iter(ordered_authors(settings, posts)), None)


def import_authors(settings: AuthorsSettings, posts: list[Post]) -> list[str]:
    """Add the authors found on the posts to the end of the list; returns the ones added."""
    added = [a for a in authors_of(posts) if a not in settings.names]
    settings.names.extend(added)
    return added


def _with_author(text: str, author: str) -> str | None:
    """`text` with `author` set in its front matter; None when it has no front matter.

    Only the `author:` line is touched (an empty one is filled in), so the rest
    of the file stays byte for byte the same. Without an author line, it goes
    right after `title:`, or before the closing delimiter.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        return None
    eol = "\r\n" if lines[0].endswith("\r\n") else "\n"
    line = f"author: {json.dumps(author, ensure_ascii=False)}{eol}"
    for i in range(1, close):
        if lines[i].startswith("author:"):
            lines[i] = line
            return "".join(lines)
    insert_at = next((i + 1 for i in range(1, close) if lines[i].startswith("title:")), close)
    lines.insert(insert_at, line)
    return "".join(lines)


def add_author_to_posts(posts: list[Post], author: str) -> list[Path]:
    """Give `author` to every post that has none; returns the files changed."""
    changed = []
    for post in posts:
        if post_author(post):
            continue
        text = post.path.read_text(encoding="utf-8")
        new_text = _with_author(text, author)
        if new_text is None:
            continue
        atomic_write_text(post.path, new_text)
        changed.append(post.path)
    return changed
