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
from hugin.embeddings import EmbeddingIndex
from hugin.fsutil import atomic_write_text
from hugin.git import commit_only, has_tracked_changes, is_repository
from hugin.project import (
    legacy_settings_path,
    load_project,
    save_project,
    settings_path,
)
from hugin.scanner import Post
from hugin.writer import write_keywords_block

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


def parse_profile(text: str) -> list[str]:
    """A link profile's comma-separated terms, without blanks or repeats (case-insensitive)."""
    seen: set[str] = set()
    terms: list[str] = []
    for term in text.split(","):
        term = term.strip()
        if term and term.lower() not in seen:
            seen.add(term.lower())
            terms.append(term)
    return terms


def fill_keywords_from_profiles(directory: Path, posts: list[Post], index: EmbeddingIndex) -> str | None:
    """Posts without a `keywords` field get their cached link profile, as one commit (not pushed).

    Runs on every startup, so a post without keywords is filled as soon as it has
    a profile. Does nothing while the blog has tracked changes, so the commit
    never picks up edits in progress.
    """
    index._load_cache()
    todo: list[tuple[Post, list[str]]] = []
    without_profile = 0
    for post in posts:
        if "keywords" in post.metadata:  # present, even if empty: left alone
            continue
        terms = parse_profile(index.get_link_keywords(post))
        if terms:
            todo.append((post, terms))
        else:
            without_profile += 1
    if not todo:
        return None

    if not is_repository(directory):
        return f"Keywords not filled for {len(todo)} post(s): the blog is not a Git repository."
    if has_tracked_changes(directory):
        return (
            f"Keywords not filled for {len(todo)} post(s): the blog has uncommitted changes. "
            "Commit or stash them, then restart Hugin."
        )

    for post, terms in todo:
        write_keywords_block(post.path, terms)
    paths = [str(post.path.resolve().relative_to(directory.resolve())) for post, _ in todo]
    ok, out = commit_only(directory, paths, f"Fill keywords from link profiles for {len(todo)} post(s)")
    if not ok:
        return f"Keywords written for {len(todo)} post(s), but the commit failed:\n{out}"

    message = f"Keywords filled from link profiles for {len(todo)} post(s), committed (not pushed)."
    if without_profile:
        message += (
            f" {without_profile} post(s) have no keywords and no link profile yet; "
            "run hugin-profiles to generate them."
        )
    return message
