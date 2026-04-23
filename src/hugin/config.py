"""Links configuration management."""

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from hugin.engines import CONFIG_DIR

DEFAULT_CONFIG = """\
[links]
max_per_post      = 8    # hard ceiling on outgoing links per post
max_per_paragraph = 1    # maximum links inserted into any single paragraph
words_per_link    = 300  # 1 link suggested per N words; result capped by max_per_post
candidates        = 10   # how many posts the embedding step returns as candidates
max_anchor_words  = 5    # maximum words in an anchor phrase (longer anchors are discarded)

[embeddings]
model = "intfloat/multilingual-e5-large"

[frontmatter]
summary_field = "description"  # field read for embedding
"""


@dataclass
class LinksConfig:
    max_per_post: int = 8
    max_per_paragraph: int = 1
    words_per_link: int = 300
    candidates: int = 10
    max_anchor_words: int = 5


@dataclass
class EmbeddingsConfig:
    model: str = "intfloat/multilingual-e5-large"


@dataclass
class FrontmatterConfig:
    summary_field: str = "description"


@dataclass
class HuginConfig:
    links: LinksConfig
    embeddings: EmbeddingsConfig
    frontmatter: FrontmatterConfig


def _resolve_config_path() -> Path:
    """Find config file: links.toml > munin.toml (with warning) > create default."""
    links_path = CONFIG_DIR / "links.toml"
    legacy_path = CONFIG_DIR / "munin.toml"

    if links_path.exists():
        return links_path

    if legacy_path.exists():
        legacy_path.rename(links_path)
        print(
            f"Migrated {legacy_path.name} → {links_path.name}",
            file=sys.stderr,
        )
        return links_path

    # Create default
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    links_path.write_text(DEFAULT_CONFIG)
    return links_path


def load_config() -> HuginConfig:
    """Load links configuration with fallback from munin.toml."""
    path = _resolve_config_path()

    with open(path, "rb") as f:
        data = tomllib.load(f)

    links_data = data.get("links", {})
    embed_data = data.get("embeddings", {})
    fm_data = data.get("frontmatter", {})

    return HuginConfig(
        links=LinksConfig(
            max_per_post=links_data.get("max_per_post", 8),
            max_per_paragraph=links_data.get("max_per_paragraph", 1),
            words_per_link=links_data.get("words_per_link", 300),
            candidates=links_data.get("candidates", 10),
            max_anchor_words=links_data.get("max_anchor_words", 5),
        ),
        embeddings=EmbeddingsConfig(
            model=embed_data.get("model", "intfloat/multilingual-e5-large"),
        ),
        frontmatter=FrontmatterConfig(
            summary_field=fm_data.get("summary_field", "description"),
        ),
    )
