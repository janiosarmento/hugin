"""Hugo config parsing and URL inference."""

import re
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

# Date prefix pattern in filenames: YYYY-MM-DD-
DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")

DEFAULT_PERMALINK = "/:section/:slug/"

SUPPORTED_TOKENS = {":slug", ":year", ":month", ":day", ":section"}

CONFIG_FILENAMES = ("hugo.toml", "config.toml", "config.yaml")


def find_hugo_config(posts_dir: Path) -> Path | None:
    """Walk up from posts_dir to find Hugo config file.

    Checks both root-level configs (hugo.toml, config.toml, config.yaml)
    and Hugo's config directory format (config/_default/*.toml).
    """
    current = posts_dir.resolve()
    while True:
        # Standard root-level config
        for name in CONFIG_FILENAMES:
            candidate = current / name
            if candidate.is_file():
                return candidate

        # Hugo config directory format: config/_default/
        config_dir = current / "config" / "_default"
        if config_dir.is_dir():
            for name in CONFIG_FILENAMES:
                candidate = config_dir / name
                if candidate.is_file():
                    return candidate

        parent = current.parent
        if parent == current:
            return None
        current = parent


def parse_hugo_config(config_path: Path) -> dict[str, Any]:
    """Parse a Hugo config file (TOML or YAML)."""
    suffix = config_path.suffix.lower()
    with open(config_path, "rb") as f:
        if suffix in (".toml",):
            return tomllib.load(f)
        elif suffix in (".yaml", ".yml"):
            return yaml.safe_load(f) or {}
    return {}


def get_permalink_pattern(config: dict[str, Any], section: str) -> str:
    """Get the permalink pattern for a given content section."""
    permalinks = config.get("permalinks", {})

    # Hugo supports both flat and nested permalink configs
    # Flat: permalinks.posts = "/posts/:slug/"
    # Nested: permalinks.page.posts = "/posts/:slug/"
    pattern = permalinks.get(section)
    if pattern:
        return pattern

    # Check nested format
    page_permalinks = permalinks.get("page", {})
    if isinstance(page_permalinks, dict):
        pattern = page_permalinks.get(section)
        if pattern:
            return pattern

    return DEFAULT_PERMALINK


def infer_section(posts_dir: Path) -> str:
    """Infer the Hugo content section from the directory structure."""
    parts = posts_dir.resolve().parts
    try:
        content_idx = parts.index("content")
        # Section is the first directory after content/
        # Handle multilingual: content/pt/posts -> section is "posts" (skip language dir)
        remaining = parts[content_idx + 1:]
        if len(remaining) >= 2 and len(remaining[0]) <= 3:
            # Likely a language code (pt, en, es, fr)
            return remaining[1]
        elif remaining:
            return remaining[0]
    except ValueError:
        pass

    # Fallback: use the directory name itself
    return posts_dir.name


def slug_from_filename(filename: str) -> str:
    """Derive slug from a markdown filename."""
    name = filename.removesuffix(".md")
    # Strip date prefix if present
    name = DATE_PREFIX_RE.sub("", name)
    return name


def _parse_date(value) -> datetime | None:
    """Parse a date from frontmatter value."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def resolve_url(
    metadata: dict,
    filename: str,
    section: str,
    permalink_pattern: str,
) -> str:
    """Resolve the URL for a single post.

    Priority:
    1. Frontmatter 'url' field (verbatim)
    2. Frontmatter 'slug' field
    3. Filename-derived slug
    """
    # 1. Explicit url in frontmatter
    url = metadata.get("url")
    if url:
        # Ensure it starts with / and ends with /
        if not url.startswith("/"):
            url = "/" + url
        if not url.endswith("/"):
            url = url + "/"
        return url

    # 2. Slug from frontmatter or filename
    slug = metadata.get("slug") or slug_from_filename(filename)

    # Parse date for token substitution
    date = _parse_date(metadata.get("date"))

    # Check for unsupported tokens
    tokens_in_pattern = set(re.findall(r":\w+", permalink_pattern))
    unsupported = tokens_in_pattern - SUPPORTED_TOKENS
    if unsupported:
        # Fallback to default pattern
        permalink_pattern = DEFAULT_PERMALINK

    # Substitute tokens
    url = permalink_pattern
    url = url.replace(":section", section)
    url = url.replace(":slug", str(slug))

    if date:
        url = url.replace(":year", str(date.year))
        url = url.replace(":month", f"{date.month:02d}")
        url = url.replace(":day", f"{date.day:02d}")
    else:
        # If date tokens exist but no date, strip them
        url = url.replace(":year/", "")
        url = url.replace(":month/", "")
        url = url.replace(":day/", "")

    # Normalize
    if not url.startswith("/"):
        url = "/" + url
    if not url.endswith("/"):
        url = url + "/"
    # Clean double slashes
    while "//" in url:
        url = url.replace("//", "/")

    return url


class HugoSite:
    """Represents a Hugo site's configuration relevant to URL inference."""

    def __init__(self, posts_dir: Path) -> None:
        self.posts_dir = posts_dir.resolve()
        self.config: dict[str, Any] = {}
        self.section = infer_section(self.posts_dir)
        self.permalink_pattern = DEFAULT_PERMALINK
        self._warnings: list[str] = []

        config_path = find_hugo_config(self.posts_dir)
        if config_path:
            self.config = parse_hugo_config(config_path)
            self.permalink_pattern = get_permalink_pattern(
                self.config, self.section,
            )
        else:
            self._warnings.append(
                "No Hugo config found. Using filename-based URLs."
            )

    @property
    def warnings(self) -> list[str]:
        return self._warnings

    def post_url(self, metadata: dict, filename: str) -> str:
        """Resolve the URL for a post."""
        return resolve_url(
            metadata, filename, self.section, self.permalink_pattern,
        )
