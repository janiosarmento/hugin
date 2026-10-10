"""Affiliate keyword → URL dictionary management."""

import tomllib
from pathlib import Path

from hugin.engines import CONFIG_DIR

AFFILIATES_PATH = CONFIG_DIR / "affiliates.toml"

DEFAULT_AFFILIATES = """\
# Affiliate keyword → URL mapping
# Each key is a keyword (or phrase) to match in post bodies.
# Each value is the affiliate URL.
#
# Example:
# arranhador = "https://amzn.to/xxx"
# "ração para gatos" = "https://amzn.to/yyy"
"""


def load_affiliates() -> dict[str, str]:
    """Load affiliate dictionary. Returns {keyword: url}."""
    if not AFFILIATES_PATH.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        AFFILIATES_PATH.write_text(DEFAULT_AFFILIATES)
        return {}

    with open(AFFILIATES_PATH, "rb") as f:
        data = tomllib.load(f)

    return {str(k): str(v) for k, v in data.items()}


def validate_affiliate(
    keyword: str, url: str, existing: dict[str, str], editing: str | None = None
) -> str | None:
    """Reason the entry is invalid, or None. `editing` is the keyword being edited."""
    if not keyword:
        return "Keyword is required."
    if not url.startswith(("http://", "https://")) or " " in url:
        return "URL must start with http:// or https:// and have no spaces."
    taken = {k.casefold() for k in existing if k != editing}
    if keyword.casefold() in taken:
        return f'"{keyword}" is already in the dictionary.'
    return None


def _edit_document(change) -> None:
    """Apply `change(doc)` to the TOML file, keeping its comments and layout."""
    import tomlkit

    load_affiliates()  # creates the file with its header comment if missing
    doc = tomlkit.parse(AFFILIATES_PATH.read_text(encoding="utf-8"))
    change(doc)
    AFFILIATES_PATH.write_text(tomlkit.dumps(doc), encoding="utf-8")


def save_affiliate(keyword: str, url: str, old_keyword: str | None = None) -> None:
    """Add an entry, or update one; a renamed keyword moves to the end."""
    def change(doc):
        if old_keyword and old_keyword != keyword and old_keyword in doc:
            del doc[old_keyword]
        doc[keyword] = url

    _edit_document(change)


def remove_affiliate(keyword: str) -> None:
    def change(doc):
        if keyword in doc:
            del doc[keyword]

    _edit_document(change)
