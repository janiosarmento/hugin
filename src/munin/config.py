"""Munin configuration management."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from hugin.engines import CONFIG_DIR

MUNIN_CONFIG_PATH = CONFIG_DIR / "munin.toml"

DEFAULT_CONFIG = """\
[links]
max_per_post      = 8    # hard ceiling on outgoing links per post
max_per_paragraph = 1    # maximum links inserted into any single paragraph
words_per_link    = 300  # 1 link suggested per N words; result capped by max_per_post
candidates        = 10   # how many posts the embedding step returns as candidates
max_anchor_words  = 5    # maximum words in an anchor phrase (longer anchors are discarded)

[embeddings]
model = "paraphrase-multilingual-MiniLM-L12-v2"

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
    model: str = "paraphrase-multilingual-MiniLM-L12-v2"


@dataclass
class FrontmatterConfig:
    summary_field: str = "description"


@dataclass
class MuninConfig:
    links: LinksConfig
    embeddings: EmbeddingsConfig
    frontmatter: FrontmatterConfig


def load_config() -> MuninConfig:
    """Load Munin configuration, creating default file if needed."""
    if not MUNIN_CONFIG_PATH.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        MUNIN_CONFIG_PATH.write_text(DEFAULT_CONFIG)

    with open(MUNIN_CONFIG_PATH, "rb") as f:
        data = tomllib.load(f)

    links_data = data.get("links", {})
    embed_data = data.get("embeddings", {})
    fm_data = data.get("frontmatter", {})

    return MuninConfig(
        links=LinksConfig(
            max_per_post=links_data.get("max_per_post", 8),
            max_per_paragraph=links_data.get("max_per_paragraph", 1),
            words_per_link=links_data.get("words_per_link", 300),
            candidates=links_data.get("candidates", 10),
            max_anchor_words=links_data.get("max_anchor_words", 5),
        ),
        embeddings=EmbeddingsConfig(
            model=embed_data.get("model", "paraphrase-multilingual-MiniLM-L12-v2"),
        ),
        frontmatter=FrontmatterConfig(
            summary_field=fm_data.get("summary_field", "description"),
        ),
    )
