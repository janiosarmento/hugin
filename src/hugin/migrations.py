"""Startup migrations for a blog's Hugin settings and posts.

Each step stands on its own and is idempotent: it looks at the blog as it is
now, does only what is still missing, and returns None once there is nothing
to do. So any blog can run the whole list, or one step, any number of times.
The order matters only where a step needs what an earlier one set up (the
author list before the default author for posts).
"""

import tomllib
from collections.abc import Callable
from pathlib import Path

from hugin.authors import add_author_to_posts, import_authors, ordered_authors, post_authors
from hugin.fsutil import atomic_write_text
from hugin.project import (
    legacy_settings_path,
    load_project,
    save_project,
    settings_path,
)
from hugin.scanner import Post

# A step returns the message describing what it changed, or None if it changed nothing
Step = Callable[[Path, list[Post]], str | None]


def copy_legacy_settings(directory: Path, posts: list[Post]) -> str | None:
    """Settings still in ~/.hugin/projects/ are copied into the site's hugin/config.toml."""
    legacy = legacy_settings_path(directory)
    if settings_path(directory).exists() or not legacy.exists():
        return None
    path = settings_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, legacy.read_text(encoding="utf-8"))
    return "Settings copied from ~/.hugin/projects/ into hugin/config.toml"


def move_author_list_to_hugo_data(directory: Path, posts: list[Post]) -> str | None:
    """A `names` list left in hugin/config.toml moves to Hugo's data/authors.toml."""
    path = settings_path(directory)
    if not path.exists():
        return None
    with open(path, "rb") as f:
        config_names = tomllib.load(f).get("authors", {}).get("names")
    if config_names is None:
        return None
    settings = load_project(directory)
    settings.authors.names = list(dict.fromkeys([*settings.authors.names, *config_names]))
    save_project(directory, settings)  # writes the list to data/authors.toml, drops it from the config
    return "Author list moved to data/authors.toml"


def add_post_authors_to_list(directory: Path, posts: list[Post]) -> str | None:
    """Authors that appear on the posts but not in the list are added to its end."""
    settings = load_project(directory)
    added = import_authors(settings.authors, posts)
    if not added:
        return None
    save_project(directory, settings)
    return f"Authors found on the posts added to the list: {', '.join(added)}"


def default_author_for_posts(directory: Path, posts: list[Post]) -> str | None:
    """Posts with no author get the first author of the list."""
    missing = [p for p in posts if not post_authors(p)]
    settings = load_project(directory).authors
    authors = ordered_authors(settings, posts)
    if not missing or not authors:
        return None
    changed = add_author_to_posts(missing, authors[0], settings.author_field)
    if not changed:
        return None
    return f"Default author {authors[0]!r} set on {len(changed)} post(s) without one."


STEPS: tuple[Step, ...] = (
    copy_legacy_settings,
    move_author_list_to_hugo_data,
    add_post_authors_to_list,
    default_author_for_posts,
)


def run_migrations(directory: Path, posts: list[Post]) -> list[str]:
    """Messages for what changed, in order; empty when the blog was already up to date."""
    messages = []
    for step in STEPS:
        message = step(directory, posts)
        if message:
            messages.append(message)
    return messages
