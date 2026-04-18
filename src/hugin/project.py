"""Per-project configuration stored in ~/.hugin/projects/<hash>.toml."""

import hashlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from hugin.engines import CONFIG_DIR

PROJECTS_DIR = CONFIG_DIR / "projects"

DEFAULT_SUMMARY_STYLE = "Write as if telling a friend — direct, with personality"


@dataclass
class SummarySettings:
    words: int = 25
    style: str = DEFAULT_SUMMARY_STYLE


@dataclass
class LinksSettings:
    words_per_link: int = 0  # 0 = use global default


@dataclass
class ProjectConfig:
    summary: SummarySettings = field(default_factory=SummarySettings)
    links: LinksSettings = field(default_factory=LinksSettings)


def _project_path(directory: Path) -> Path:
    dir_hash = hashlib.sha256(str(directory.resolve()).encode()).hexdigest()[:16]
    return PROJECTS_DIR / f"{dir_hash}.toml"


def load_project(directory: Path) -> ProjectConfig:
    path = _project_path(directory)
    if not path.exists():
        return ProjectConfig()

    with open(path, "rb") as f:
        data = tomllib.load(f)

    summary_data = data.get("summary", {})
    links_data = data.get("links", {})
    return ProjectConfig(
        summary=SummarySettings(
            words=summary_data.get("words", 25),
            style=summary_data.get("style", DEFAULT_SUMMARY_STYLE),
        ),
        links=LinksSettings(
            words_per_link=links_data.get("words_per_link", 0),
        ),
    )


def save_project(directory: Path, config: ProjectConfig) -> None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _project_path(directory)

    lines = [
        "[summary]",
        f"words = {config.summary.words}",
        f'style = "{config.summary.style}"',
        "",
        "[links]",
        f"words_per_link = {config.links.words_per_link}  # 0 = use global default",
        "",
    ]
    path.write_text("\n".join(lines))
