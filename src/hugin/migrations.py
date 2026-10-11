"""Startup migrations for a blog's Hugin settings and posts.

Each migration stands on its own and is idempotent: it looks at the blog as
it is now, does only what is still missing, and does nothing once it is
done. So any blog can run the whole list, or a single step, any number of
times. The order only matters where a step needs what an earlier one set up
(the author list before the default author for posts).
"""

import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from hugin.authors import add_author_to_posts, default_author, import_authors, post_authors
from hugin.fsutil import atomic_write_text
from hugin.project import (
    legacy_settings_path,
    load_project,
    save_project,
    settings_path,
)
from hugin.scanner import Post


@dataclass
class Outcome:
    changed: bool = False
    messages: list[str] = field(default_factory=list)


Migration = Callable[[Path, list[Post]], Outcome]


def copy_legacy_settings(directory: Path, posts: list[Post]) -> Outcome:
    """Settings still in ~/.hugin/projects/ are copied into the site's hugin/config.toml."""
    legacy = legacy_settings_path(directory)
    if settings_path(directory).exists() or not legacy.exists():
        return Outcome()
    path = settings_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, legacy.read_text(encoding="utf-8"))
    return Outcome(True, ["Settings copied from ~/.hugin/projects/ into hugin/config.toml"])


def move_author_list_to_hugo_data(directory: Path, posts: list[Post]) -> Outcome:
    """A `names` list left in hugin/config.toml moves to Hugo's data/authors.toml."""
    path = settings_path(directory)
    if not path.exists():
        return Outcome()
    with open(path, "rb") as f:
        config_names = tomllib.load(f).get("authors", {}).get("names")
    if config_names is None:
        return Outcome()
    settings = load_project(directory)
    settings.authors.names = list(dict.fromkeys([*settings.authors.names, *config_names]))
    save_project(directory, settings)  # writes the list to data/authors.toml, drops it from the config
    return Outcome(True, ["Author list moved to data/authors.toml"])


def add_post_authors_to_list(directory: Path, posts: list[Post]) -> Outcome:
    """Authors that appear on the posts but not in the list are added to its end."""
    settings = load_project(directory)
    added = import_authors(settings.authors, posts)
    if not added:
        return Outcome()
    save_project(directory, settings)
    return Outcome(True, [f"Authors found on the posts added to the list: {', '.join(added)}"])


def default_author_for_posts(directory: Path, posts: list[Post]) -> Outcome:
    """Posts with no author get the first author of the list."""
    missing = [p for p in posts if not post_authors(p)]
    if not missing:
        return Outcome()
    author = default_author(load_project(directory).authors, posts)
    if author is None:
        return Outcome(False, [
            f"Warning: {len(missing)} post(s) have no author and there is no author list "
            "to take a default from. Add authors in the project settings.",
        ])
    changed = add_author_to_posts(missing, author)
    if not changed:
        return Outcome()
    return Outcome(True, [f"Default author {author!r} set on {len(changed)} post(s) without one."])


MIGRATIONS: tuple[Migration, ...] = (
    copy_legacy_settings,
    move_author_list_to_hugo_data,
    add_post_authors_to_list,
    default_author_for_posts,
)


def run_migrations(directory: Path, posts: list[Post]) -> Outcome:
    """Run every migration in order; `changed` is True if any of them changed files."""
    total = Outcome()
    for migration in MIGRATIONS:
        outcome = migration(directory, posts)
        total.changed |= outcome.changed
        total.messages += outcome.messages
    return total
