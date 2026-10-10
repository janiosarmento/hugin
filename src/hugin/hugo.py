"""Hugo config parsing and URL inference."""

import re
import tomllib
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import tomlkit
import yaml

from hugin.log import log_exception

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


def _parse_date(value: Any) -> datetime | None:
    """Parse a date from a frontmatter value."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


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
    slug = metadata.get("slug") or slug_from_filename(filename)

    date = _parse_date(metadata.get("date"))

    # Unsupported tokens fall back to the default pattern
    tokens_in_pattern = set(re.findall(r":\w+", permalink_pattern))
    if tokens_in_pattern - SUPPORTED_TOKENS:
        permalink_pattern = DEFAULT_PERMALINK

    url = permalink_pattern
    url = url.replace(":section", section)
    url = url.replace(":slug", str(slug))

    if date:
        url = url.replace(":year", str(date.year))
        url = url.replace(":month", f"{date.month:02d}")
        url = url.replace(":day", f"{date.day:02d}")
    else:
        # Date tokens with no date available: strip the whole path segment
        url = url.replace(":year/", "")
        url = url.replace(":month/", "")
        url = url.replace(":day/", "")

    if not url.startswith("/"):
        url = "/" + url
    if not url.endswith("/"):
        url += "/"
    while "//" in url:
        url = url.replace("//", "/")

    return url


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
    """Derive a slug from a filename."""
    stem = Path(filename).stem
    # Strip YYYY-MM-DD- date prefix
    stem = DATE_PREFIX_RE.sub("", stem)
    return stem


def slugify(text: str) -> str:
    """Convert arbitrary text (e.g. a title) into a URL-friendly slug."""
    s = unicodedata.normalize("NFKD", text.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s.strip())
    s = re.sub(r"-+", "-", s).strip("-")
    return s


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
                cats = _extract_collection_categories(data, root, posts_dir)
                if not cats:
                    cats = _extract_pages_cms_categories(data)
                if cats:
                    return cats
            except (OSError, yaml.YAMLError):
                # A broken .pages.yml must not stop the search, but leave a trace
                log_exception(f"read {pages_cfg}")

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


def _select_option_values(options: Any) -> list[str]:
    """Stored values of a Pages CMS select field.

    `options` is either a plain list or a dict with a `values` list; each
    entry is a string or a {label, value} mapping (the value is what ends
    up in the frontmatter).
    """
    if isinstance(options, dict):
        options = options.get("values")
    if not isinstance(options, list):
        return []
    out = []
    for item in options:
        if isinstance(item, dict):
            item = item.get("value", item.get("label"))
        if item:
            out.append(str(item))
    return out


def _extract_collection_categories(data: Any, root: Path, posts_dir: Path) -> list[str]:
    """Categories of the Pages CMS collection whose `path` is `posts_dir`.

    Multilingual sites declare one collection per language, each with its own
    category list; picking by path keeps an English blog on English categories.
    """
    if not isinstance(data, dict) or not isinstance(data.get("content"), list):
        return []
    target = posts_dir.resolve()
    for item in data["content"]:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        if (root / str(item["path"])).resolve() == target:
            return _extract_pages_cms_categories(item)
    return []


def _extract_pages_cms_categories(data: Any) -> list[str]:
    """Walk a Pages CMS config dict to find a select field named categories/category."""
    if not isinstance(data, dict):
        return []

    # Check if this node is a select field with the right name
    if data.get("type") == "select" and data.get("name") in ("category", "categories"):
        values = _select_option_values(data.get("options"))
        if values:
            return values

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
        self.section = infer_section(self.posts_dir)
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
