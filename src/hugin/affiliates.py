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
