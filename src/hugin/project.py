"""Per-project configuration stored in ~/.hugin/projects/<hash>.toml."""

import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from hugin.engines import CONFIG_DIR
from hugin.fsutil import atomic_write_text

PROJECTS_DIR = CONFIG_DIR / "projects"

DEFAULT_SUMMARY_STYLE = "Write as if telling a friend — direct, with personality"

# Editorial rules sent with every Echo / system-LLM draft request. Per blog,
# overridable per detected language (see WritingSettings.for_language).
DEFAULT_EDITORIAL_CONSTRAINTS = """\
Write a complete first draft using the supplied writing samples as stylistic references, not as sources of invented personal experiences.

Avoid formulaic contrasts such as "it's not X, it's Y", especially when repeated. Avoid motivational conclusions, corporate language, generic metaphors, and rhetorical filler.

Do not invent personal anecdotes, events, achievements, or opinions not supported by the prompt or reference material.

Develop each argument once, thoroughly, rather than repeating it in different words.

Preserve nuance. Avoid absolute claims unless they are justified.

The article must have a natural ending, even if the conclusion is uncertain, understated, or deliberately anticlimactic.

Favor concrete observations, conversational language, dry humor, and occasional self-deprecation. Do not force jokes or metaphors.

Stylistic devices (em-dashes, parallel structures, short punchy sentences, rhetorical questions, contrasts) are fine when they serve a sentence. Abusing any one of them is not. Do not let a device become a habit: no recurring em-dash pattern, no string of short sentences in a row, no repeated "it's not X, it's Y" construction, and no device used again just because it worked earlier in the text.

The goal is a draft that requires minimal editorial correction, not an imitation of superficial stylistic quirks."""


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
class ProjectConfig:
    summary: SummarySettings = field(default_factory=SummarySettings)
    links: LinksSettings = field(default_factory=LinksSettings)
    writing: WritingSettings = field(default_factory=WritingSettings)


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
    writing_data = data.get("writing", {})
    return ProjectConfig(
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
    atomic_write_text(path, "\n".join(lines))


def _toml_string(text: str) -> str:
    """Quote text as a TOML basic string (JSON string escapes are valid TOML)."""
    return json.dumps(text, ensure_ascii=False)
