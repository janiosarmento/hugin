"""Per-site configuration: hugin/config.toml, and the author list in Hugo data/authors.toml."""

import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from hugin import prompts
from hugin.engines import CONFIG_DIR
from hugin.fsutil import atomic_write_text
from hugin.hugo import find_site_root

PROJECTS_DIR = CONFIG_DIR / "projects"  # legacy location, read as a fallback
SETTINGS_DIR = "hugin"  # inside the blog repository
SETTINGS_FILE = "config.toml"
AUTHORS_DATA_FILE = Path("data") / "authors.toml"  # Hugo data, at the site root

DEFAULT_SUMMARY_STYLE = "Write as if telling a friend — direct, with personality"
DEFAULT_AUTHOR_FIELD = "author"

# Editorial rules sent with every LLM draft request. Per blog,
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
    # Front matter key the blog uses: "author" (one name) or "authors" (a list, Hugo's taxonomy)
    author_field: str = DEFAULT_AUTHOR_FIELD
    # Voice per author, keyed by the `author` front matter value
    profiles: dict[str, str] = field(default_factory=dict)


@dataclass
class ImagesSettings:
    """Where the blog keeps new images (see hugin.images); empty until confirmed."""

    dir: str = ""  # folder relative to the site root, e.g. "static/images"
    url: str = ""  # link prefix, e.g. "/images"
    date_folders: str = ""  # "", "YYYY", "YYYY/MM" or "YYYY/MM/DD"

    @property
    def configured(self) -> bool:
        return bool(self.dir and self.url)


@dataclass
class ProjectConfig:
    summary: SummarySettings = field(default_factory=SummarySettings)
    links: LinksSettings = field(default_factory=LinksSettings)
    writing: WritingSettings = field(default_factory=WritingSettings)
    authors: AuthorsSettings = field(default_factory=AuthorsSettings)
    images: ImagesSettings = field(default_factory=ImagesSettings)
    # LLM prompts the blog changed, keyed as in hugin.prompts.PROMPTS
    prompts: dict[str, str] = field(default_factory=dict)


def settings_path(directory: Path) -> Path:
    """The site's settings file, at the Hugo site root (the file saves write).

    `directory` is the posts folder; the settings are shared by all of the
    site's languages, so they live at the root, not in one language's folder.
    """
    return find_site_root(directory) / SETTINGS_DIR / SETTINGS_FILE


def legacy_settings_path(directory: Path) -> Path:
    """Where settings lived before they moved into the blog; read as a fallback."""
    dir_hash = hashlib.sha256(str(directory.resolve()).encode()).hexdigest()[:16]
    return PROJECTS_DIR / f"{dir_hash}.toml"


def authors_path(directory: Path) -> Path:
    """The author list: Hugo data at the site root, so the theme and Hugin read the same names."""
    return find_site_root(directory) / AUTHORS_DATA_FILE


def _read_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_project(directory: Path) -> ProjectConfig:
    path = settings_path(directory)
    if not path.exists():
        path = legacy_settings_path(directory)
    authors_file = authors_path(directory)
    if not path.exists() and not authors_file.exists():
        prompts.activate({})
        return ProjectConfig()

    data = _read_toml(path) if path.exists() else {}
    summary_data = data.get("summary", {})
    links_data = data.get("links", {})
    writing_data = data.get("writing", {})
    authors_data = data.get("authors", {})
    images_data = data.get("images", {})
    # Older settings kept the author list here; the Hugo data file wins once it exists
    if authors_file.exists():
        names = _read_toml(authors_file).get("names", [])
    else:
        names = authors_data.get("names", [])
    overrides = prompts.only_changed(dict(data.get("prompts", {})))
    prompts.activate(overrides)  # the loaded site's prompts are the ones sent
    return ProjectConfig(
        prompts=overrides,
        authors=_authors_settings(names, authors_data.get("profiles", {}), authors_data.get("field", DEFAULT_AUTHOR_FIELD)),
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
        images=ImagesSettings(
            dir=str(images_data.get("dir", "")),
            url=str(images_data.get("url", "")),
            date_folders=str(images_data.get("date_folders", "")),
        ),
    )


def save_project(directory: Path, config: ProjectConfig) -> None:
    path = settings_path(directory)
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
    if authors.author_field != DEFAULT_AUTHOR_FIELD or authors.profiles:
        lines += ["[authors]"]
        if authors.author_field != DEFAULT_AUTHOR_FIELD:
            lines.append(f"field = {_toml_string(authors.author_field)}")
        if authors.profiles:
            lines += ["", "[authors.profiles]"]
            lines += [
                f"{_toml_string(author)} = {_toml_string(text)}"
                for author, text in authors.profiles.items()
            ]
        lines.append("")
    images = config.images
    if images.configured:
        lines += [
            "[images]",
            "# Where images pasted in an external editor go; see hugin/images.py",
            f"dir = {_toml_string(images.dir)}",
            f"url = {_toml_string(images.url)}",
            f"date_folders = {_toml_string(images.date_folders)}",
            "",
        ]
    config.prompts = prompts.only_changed(config.prompts)
    if config.prompts:
        lines += ["[prompts]", "# Only the prompts changed from Hugin's defaults; see hugin/prompts.py"]
        lines += [
            f"{_toml_string(key)} = {_toml_text(text)}" for key, text in config.prompts.items()
        ]
        lines.append("")
    atomic_write_text(path, "\n".join(lines))
    prompts.activate(config.prompts)

    if authors.names:
        authors_file = authors_path(directory)
        authors_file.parent.mkdir(parents=True, exist_ok=True)
        names = ", ".join(_toml_string(n) for n in authors.names)
        atomic_write_text(
            authors_file,
            "# Authors of the blog, in order. The first one is the default for posts without an author.\n"
            f"names = [{names}]\n",
        )


def _authors_settings(names: list[str], profiles: dict[str, str], author_field: str) -> AuthorsSettings:
    """The list, then any profiled author missing from it."""
    profiles = dict(profiles)
    return AuthorsSettings(
        names=list(dict.fromkeys([*names, *profiles])), profiles=profiles, author_field=author_field,
    )


def _toml_text(text: str) -> str:
    """Long text as a TOML multi-line literal string, readable in a diff; quoted when it can't be."""
    if "\n" not in text or "\'\'\'" in text or text.endswith("\'") or any(
        ord(c) < 32 and c not in "\n\t" for c in text
    ):
        return _toml_string(text)
    return f"\'\'\'\n{text}\'\'\'"


def _toml_string(text: str) -> str:
    """Quote text as a TOML basic string (JSON string escapes are valid TOML)."""
    return json.dumps(text, ensure_ascii=False)
