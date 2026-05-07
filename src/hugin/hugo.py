"""Hugo config parsing and URL inference."""

import re
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

import tomlkit
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


def resolve_url(
    metadata: dict[str, Any],
    filename: str,
    section: str,
    permalink_pattern: str,
) -> str:
    """Resolve the canonical URL for a post."""
    # Frontmatter url overrides everything
    if "url" in metadata:
        url = str(metadata["url"])
        if not url.startswith("/"):
            url = "/" + url
        if not url.endswith("/"):
            url += "/"
        return url

    # Slug from frontmatter or filename
    slug = metadata.get("slug") or _slug_from_filename(filename)

    date = metadata.get("date")
    if date and hasattr(date, "year"):
        year = str(date.year)
        month = f"{date.month:02d}"
        day = f"{date.day:02d}"
    else:
        year = month = day = ""

    url = permalink_pattern
    url = url.replace(":slug", slug)
    url = url.replace(":section", section)
    url = url.replace(":year", year)
    url = url.replace(":month", month)
    url = url.replace(":day", day)

    if not url.startswith("/"):
        url = "/" + url
    if not url.endswith("/"):
        url += "/"

    return url


def _slug_from_filename(filename: str) -> str:
    """Derive a slug from a filename."""
    stem = Path(filename).stem
    # Strip YYYY-MM-DD- date prefix
    stem = DATE_PREFIX_RE.sub("", stem)
    return stem


def ensure_ignored_in_hugo(posts_dir: Path, filenames: list[str]) -> list[str]:
    """Ensure filenames appear in Hugo's ignoreFiles config.

    Finds the Hugo config file, adds any missing entries to ignoreFiles
    as anchored regex patterns (e.g. CLAUDE\\.md → "^CLAUDE\\.md$"),
    and saves the file in-place preserving formatting.

    Returns a list of filenames that were actually added (empty if all
    were already present or no Hugo config was found).
    """
    config_path = find_hugo_config(posts_dir)
    if not config_path:
        return []

    suffix = config_path.suffix.lower()
    # Build anchored regex patterns for each filename
    patterns = {name: f"^{re.escape(name)}$" for name in filenames}

    if suffix == ".toml":
        return _ensure_ignored_toml(config_path, patterns)
    elif suffix in (".yaml", ".yml"):
        return _ensure_ignored_yaml(config_path, patterns)
    return []


def _ensure_ignored_toml(config_path: Path, patterns: dict[str, str]) -> list[str]:
    """Add missing ignoreFiles patterns to a TOML Hugo config."""
    text = config_path.read_text(encoding="utf-8")
    doc = tomlkit.parse(text)

    existing: list[str] = list(doc.get("ignoreFiles", []))
    added = []
    for name, pattern in patterns.items():
        if pattern not in existing:
            existing.append(pattern)
            added.append(name)

    if added:
        doc["ignoreFiles"] = existing
        config_path.write_text(tomlkit.dumps(doc), encoding="utf-8")

    return added


def _ensure_ignored_yaml(config_path: Path, patterns: dict[str, str]) -> list[str]:
    """Add missing ignoreFiles patterns to a YAML Hugo config."""
    text = config_path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}

    existing: list[str] = list(data.get("ignoreFiles", []))
    added = []
    for name, pattern in patterns.items():
        if pattern not in existing:
            existing.append(pattern)
            added.append(name)

    if added:
        data["ignoreFiles"] = existing
        config_path.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )

    return added


def load_categories(posts_dir: Path) -> list[str]:
    """Discover available post categories, searching up from posts_dir.

    Checks in order:
    1. .pages.yml — Pages CMS config (select field named 'categories' or 'category')
    2. Hugo taxonomy config — looks for 'categories' taxonomy values in hugo.toml

    Returns an empty list if nothing is found.
    """
    root = posts_dir.resolve()
    while True:
        # Pages CMS
        pages_cfg = root / ".pages.yml"
        if pages_cfg.is_file():
            try:
                with open(pages_cfg) as f:
                    data = yaml.safe_load(f)
                cats = _extract_pages_cms_categories(data)
                if cats:
                    return cats
            except Exception:
                pass

        parent = root.parent
        if parent == root:
            break
        root = parent

    # Fallback: Hugo taxonomy
    config_path = find_hugo_config(posts_dir)
    if config_path:
        config = parse_hugo_config(config_path)
        taxonomies = config.get("taxonomies", {})
        if "category" in taxonomies or "categories" in taxonomies:
            # Taxonomy is defined but values aren't in config; return empty
            pass

    return []


def _extract_pages_cms_categories(data: Any) -> list[str]:
    """Walk a Pages CMS config dict to find a select field named categories/category."""
    if not isinstance(data, dict):
        return []

    # Check if this node is a select field with the right name
    if (
        data.get("type") == "select"
        and data.get("name", "").rstrip("s") == "categor"  # category or categories
        and isinstance(data.get("options"), list)
    ):
        return [str(o) for o in data["options"] if o]

    # Recurse into all dict values and lists
    for value in data.values():
        if isinstance(value, dict):
            result = _extract_pages_cms_categories(value)
            if result:
                return result
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    result = _extract_pages_cms_categories(item)
                    if result:
                        return result

    return []


class HugoSite:
    """Resolves Hugo post URLs using the site's permalink configuration."""

    def __init__(self, posts_dir: Path) -> None:
        self.posts_dir = posts_dir.resolve()
        self.section = posts_dir.name
        self.config: dict[str, Any] = {}
        self.permalink_pattern = DEFAULT_PERMALINK
        self._warnings: list[str] = []

        config_path = find_hugo_config(posts_dir)
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
