"""Per-blog configuration, stored in the blog at hugin/config.toml."""

import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from hugin.engines import CONFIG_DIR
from hugin.fsutil import atomic_write_text
from hugin.hugo import find_site_root

PROJECTS_DIR = CONFIG_DIR / "projects"  # legacy location, read as a fallback
SETTINGS_DIR = "hugin"  # inside the blog repository
SETTINGS_FILE = "config.toml"

DEFAULT_SUMMARY_STYLE = "Write as if telling a friend — direct, with personality"

# Editorial rules sent with every Echo / system-LLM draft request. Per blog,
# overridable per detected language (see WritingSettings.for_language).
DEFAULT_EDITORIAL_CONSTRAINTS = """\
Write in the voice of the writing samples: their vocabulary, their rhythm, their level of formality.
Vary sentence length. Use stylistic devices when they help the sentence; repeating one is worse than leaving it out.
Do not invent experiences, facts or opinions that the request does not contain.
Stop when the point is made."""


@dataclass
class SummarySettings:
    words: int = 25
    style: str = DEFAULT_SUMMARY_STYLE


@dataclass
class LinksSettings:
    words_per_link: int = 0  # 0 = use global default


@dataclass
class WritingSettings:
    constraints: str = DEFAULT_EDITORIAL_CONSTRAINTS
    # Keyed by language name as detect_language() returns it ("Portuguese", ...)
    by_language: dict[str, str] = field(default_factory=dict)

    def for_language(self, language: str) -> str:
        return self.by_language.get(language, self.constraints)


@dataclass
class AuthorsSettings:
    # The author list, in order: the first one is the default for posts without an author
    names: list[str] = field(default_factory=list)
    # Voice per author, keyed by the `author` front matter value
    profiles: dict[str, str] = field(default_factory=dict)

    def profile_for(self, author: str | None) -> str | None:
        return self.profiles.get(author or "") or None


@dataclass
class ProjectConfig:
    summary: SummarySettings = field(default_factory=SummarySettings)
    links: LinksSettings = field(default_factory=LinksSettings)
    writing: WritingSettings = field(default_factory=WritingSettings)
    authors: AuthorsSettings = field(default_factory=AuthorsSettings)


def _project_path(directory: Path) -> Path:
    """The site's settings file, at the Hugo site root (the file saves write).

    `directory` is the posts folder; the settings are shared by all of the
    site's languages, so they live at the root, not in one language's folder.
    """
    return find_site_root(directory) / SETTINGS_DIR / SETTINGS_FILE


def _legacy_path(directory: Path) -> Path:
    """Where settings lived before they moved into the blog; read as a fallback."""
    dir_hash = hashlib.sha256(str(directory.resolve()).encode()).hexdigest()[:16]
    return PROJECTS_DIR / f"{dir_hash}.toml"


def load_project(directory: Path) -> ProjectConfig:
    path = _project_path(directory)
    if not path.exists():
        path = _legacy_path(directory)
    if not path.exists():
        return ProjectConfig()

    with open(path, "rb") as f:
        data = tomllib.load(f)

    summary_data = data.get("summary", {})
    links_data = data.get("links", {})
    writing_data = data.get("writing", {})
    authors_data = data.get("authors", {})
    return ProjectConfig(
        authors=_authors_settings(authors_data),
        summary=SummarySettings(
            words=summary_data.get("words", 25),
            style=summary_data.get("style", DEFAULT_SUMMARY_STYLE),
        ),
        links=LinksSettings(
            words_per_link=links_data.get("words_per_link", 0),
        ),
        writing=WritingSettings(
            constraints=writing_data.get("constraints", DEFAULT_EDITORIAL_CONSTRAINTS),
            by_language=dict(writing_data.get("by_language", {})),
        ),
    )


def save_project(directory: Path, config: ProjectConfig) -> None:
    path = _project_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "[summary]",
        f"words = {config.summary.words}",
        f'style = "{config.summary.style}"',
        "",
        "[links]",
        f"words_per_link = {config.links.words_per_link}  # 0 = use global default",
        "",
    ]
    writing = config.writing
    # Written only when changed, so the built-in default keeps evolving with the code
    if writing.constraints != DEFAULT_EDITORIAL_CONSTRAINTS or writing.by_language:
        lines += ["[writing]", f"constraints = {_toml_string(writing.constraints)}"]
        if writing.by_language:
            lines += ["", "[writing.by_language]"]
            lines += [
                f"{_toml_string(language)} = {_toml_string(text)}"
                for language, text in writing.by_language.items()
            ]
        lines.append("")
    authors = config.authors
    if authors.names or authors.profiles:
        lines += ["[authors]", f"names = [{', '.join(_toml_string(n) for n in authors.names)}]"]
        if authors.profiles:
            lines += ["", "[authors.profiles]"]
            lines += [
                f"{_toml_string(author)} = {_toml_string(text)}"
                for author, text in authors.profiles.items()
            ]
        lines.append("")
    atomic_write_text(path, "\n".join(lines))


def _authors_settings(data: dict) -> AuthorsSettings:
    """Names first, then any profiled author missing from them (older files have profiles only)."""
    profiles = dict(data.get("profiles", {}))
    names = list(dict.fromkeys([*data.get("names", []), *profiles]))
    return AuthorsSettings(names=names, profiles=profiles)


def _toml_string(text: str) -> str:
    """Quote text as a TOML basic string (JSON string escapes are valid TOML)."""
    return json.dumps(text, ensure_ascii=False)
