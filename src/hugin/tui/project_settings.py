"""Project settings screen, and the editorial rules screen it opens."""

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, TextArea

from hugin.git import SETTINGS_PUSHED, commit_settings
from hugin.hugo import find_site_root
from hugin.project import DEFAULT_EDITORIAL_CONSTRAINTS, ProjectConfig, save_project


def save_and_sync(app, directory: Path, config: ProjectConfig, what: str) -> None:
    """Save the settings file, then commit and push it on a worker thread.

    The commit is for the settings file alone, so a sync is never blocked on posts.
    """
    save_project(directory, config)
    root = find_site_root(directory)

    def sync() -> None:
        line = commit_settings(root, f"Hugin settings: update {what}")
        if line:
            severity = "information" if line == SETTINGS_PUSHED else "warning"
            app.call_from_thread(app.notify, line, severity=severity, timeout=10)

    app.run_worker(sync, thread=True)


class ProjectSettingsScreen(ModalScreen[bool]):
    """Modal to edit per-project settings."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    ProjectSettingsScreen {
        align: center middle;
    }

    #settings-modal {
        width: 70;
        height: auto;
        max-height: 100%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #settings-modal Label {
        margin-bottom: 0;
    }

    .settings-title {
        text-style: bold;
        margin-bottom: 1;
    }

    .field-label {
        margin-top: 1;
        text-style: bold;
    }

    .field-hint {
        color: $text-muted;
        margin-bottom: 0;
    }

    #settings-buttons {
        height: auto;
        margin-top: 2;
    }

    #settings-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(
        self, config: ProjectConfig, directory: Path, global_words_per_link: int = 300,
        authors: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._directory = directory
        self._global_wpl = global_words_per_link
        self._authors = authors or []

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="settings-modal"):
            yield Label("Project Settings", classes="settings-title")

            yield Label("Summary words", classes="field-label")
            yield Static("Target word count for generated summaries", classes="field-hint")
            yield Input(
                value=str(self._config.summary.words),
                id="input-words",
                type="integer",
            )

            yield Label("Summary style", classes="field-label")
            yield Static("Tone instruction appended to the summary prompt", classes="field-hint")
            yield Input(
                value=self._config.summary.style,
                id="input-style",
            )

            yield Label("Words per link", classes="field-label")
            yield Static(f"1 link per N words (0 = use global default: {self._global_wpl})", classes="field-hint")
            yield Input(
                value=str(self._config.links.words_per_link),
                id="input-words-per-link",
                type="integer",
            )

            yield Label("Editorial rules", classes="field-label")
            yield Static(
                "Guidelines sent with every Echo draft of this blog, and per-language overrides.",
                classes="field-hint",
            )
            yield Button("Editorial rules…", id="btn-editorial-rules")

            yield Label("Author profiles", classes="field-label")
            yield Static(
                "Voice and style of each author, sent with their Echo drafts. Not the public author bio.",
                classes="field-hint",
            )
            yield Button("Author profiles…", id="btn-author-profiles")

            with Horizontal(id="settings-buttons"):
                yield Button("Save", id="btn-save-settings", variant="primary")
                yield Button("Cancel", id="btn-cancel-settings")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save-settings":
            self._do_save()
        elif event.button.id == "btn-editorial-rules":
            self.app.push_screen(EditorialRulesScreen(self._config, self._directory))
        elif event.button.id == "btn-author-profiles":
            self.app.push_screen(AuthorProfilesScreen(self._config, self._directory, self._authors))
        else:
            self.dismiss(False)

    def _do_save(self) -> None:
        words_str = self.query_one("#input-words", Input).value.strip()
        style = self.query_one("#input-style", Input).value.strip()
        wpl_str = self.query_one("#input-words-per-link", Input).value.strip()

        try:
            words = int(words_str)
            if words < 5:
                words = 5
            if words > 50:
                words = 50
        except ValueError:
            words = self._config.summary.words

        try:
            wpl = int(wpl_str)
            if wpl < 0:
                wpl = 0
        except ValueError:
            wpl = self._config.links.words_per_link

        self._config.summary.words = words
        self._config.summary.style = style or self._config.summary.style
        self._config.links.words_per_link = wpl

        save_and_sync(self.app, self._directory, self._config, "summary and links")
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class AuthorProfilesScreen(ModalScreen[bool]):
    """Voice profile per author; saves straight to the project file.

    The profile is part of the prompt, not the public bio. A blank profile
    removes the author's entry. Switching author discards unsaved edits.
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    AuthorProfilesScreen {
        align: center middle;
    }

    #profiles-modal {
        width: 90%;
        max-width: 110;
        height: 100%;
        max-height: 100%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #profiles-modal .settings-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #profiles-modal .field-label {
        margin-top: 1;
        text-style: bold;
    }

    #profiles-modal .field-hint {
        color: $text-muted;
    }

    #select-profile-author {
        width: 50;
    }

    #input-profile-name {
        width: 50;
    }

    #input-profile-text {
        height: 1fr;
        min-height: 4;
    }

    #profiles-buttons {
        height: auto;
        margin-top: 1;
    }

    #profiles-buttons Button {
        margin: 0 1 0 0;
    }
    """

    def __init__(self, config: ProjectConfig, directory: Path, authors: list[str]) -> None:
        super().__init__()
        self._config = config
        self._directory = directory
        self._profiles = config.authors.profiles
        # The configured order, then authors found only on the posts
        self._authors = list(dict.fromkeys([*config.authors.names, *authors]))
        # The author loaded in the form; renaming it in the form moves the profile
        self._loaded = self._authors[0] if self._authors else ""

    def compose(self) -> ComposeResult:
        with Vertical(id="profiles-modal"):
            yield Label("Author profiles", classes="settings-title")

            yield Label("Author", classes="field-label")
            yield Static(
                "Pick an author to edit, or leave blank to add a new one. "
                "The name must match the author front matter of the posts.",
                classes="field-hint",
            )
            yield Select(
                [(a, a) for a in self._authors],
                value=self._loaded or Select.NULL,
                allow_blank=True,
                id="select-profile-author",
            )
            yield Input(value=self._loaded, placeholder="Author name", id="input-profile-name")

            yield Label("Profile", classes="field-label")
            yield Static(
                "Voice and style to write in, e.g. formal and academic, or warm and informal. "
                "Blank removes this author's profile.",
                classes="field-hint",
            )
            yield TextArea(self._profiles.get(self._loaded, ""), id="input-profile-text")

            with Horizontal(id="profiles-buttons"):
                yield Button("Save", id="btn-save-profile", variant="primary")
                yield Button("Cancel", id="btn-cancel-profile")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "select-profile-author" or event.value in (Select.NULL, self._loaded):
            return
        self._loaded = event.value
        self.query_one("#input-profile-name", Input).value = self._loaded
        self.query_one("#input-profile-text", TextArea).load_text(self._profiles.get(self._loaded, ""))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save-profile":
            self._do_save()
        else:
            self.dismiss(False)

    def _do_save(self) -> None:
        name = self.query_one("#input-profile-name", Input).value.strip()
        text = self.query_one("#input-profile-text", TextArea).text.strip()

        if not name:
            self.notify("Name the author before saving the profile.", severity="warning")
            return
        if name != self._loaded and name in self._config.authors.names:
            self.notify(f"{name} is already in the list; pick it from there.", severity="warning")
            return

        names = self._config.authors.names
        if self._loaded and self._loaded != name:
            # Renamed in place: the order of the list (its first author is the default) is kept
            if self._loaded in names:
                names[names.index(self._loaded)] = name
            if self._loaded in self._profiles:
                renamed = [(name if a == self._loaded else a, t) for a, t in self._profiles.items()]
                self._profiles.clear()
                self._profiles.update(renamed)
        if name not in names:
            names.append(name)
        if text:
            self._profiles[name] = text
        else:
            self._profiles.pop(name, None)

        save_and_sync(self.app, self._directory, self._config, "author profiles")
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class EditorialRulesScreen(ModalScreen[bool]):
    """Blog-wide editorial rules, plus one override per detected language.

    Saves straight to the project file, so the rules take effect without
    going back through the settings screen.
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    EditorialRulesScreen {
        align: center middle;
    }

    #rules-modal {
        width: 90%;
        max-width: 110;
        height: 100%;
        max-height: 100%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #rules-modal .settings-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #rules-modal .field-label {
        margin-top: 1;
        text-style: bold;
    }

    #rules-modal .field-hint {
        color: $text-muted;
    }

    #input-constraints {
        height: 1fr;
        min-height: 3;
    }

    #input-override-rules {
        height: 4;
    }

    #rules-buttons {
        height: auto;
        margin-top: 1;
    }

    #rules-buttons Button {
        margin: 0 1 0 0;
    }
    """

    def __init__(self, config: ProjectConfig, directory: Path) -> None:
        super().__init__()
        self._config = config
        self._directory = directory
        overrides = config.writing.by_language
        # The override shown first; renaming it in the form moves it instead of copying it
        self._loaded_language = next(iter(overrides), "")

    def compose(self) -> ComposeResult:
        writing = self._config.writing
        overrides = ", ".join(writing.by_language) or "none"
        with Vertical(id="rules-modal"):
            yield Label("Editorial rules", classes="settings-title")

            yield Label("Blog rules", classes="field-label")
            yield Static(
                "Sent with every Echo draft of this blog. Blank = Hugin default.",
                classes="field-hint",
            )
            yield TextArea(writing.constraints, id="input-constraints")

            yield Label("Language override", classes="field-label")
            yield Static(
                "Language as detected from the posts (Portuguese, English, Spanish, French). "
                "Blank rules remove the override. Current overrides: " + overrides,
                classes="field-hint",
            )
            yield Input(value=self._loaded_language, id="input-override-language")
            yield TextArea(
                writing.by_language.get(self._loaded_language, ""),
                id="input-override-rules",
            )

            with Horizontal(id="rules-buttons"):
                yield Button("Save", id="btn-save-rules", variant="primary")
                yield Button("Restore default", id="btn-restore-constraints")
                yield Button("Cancel", id="btn-cancel-rules")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save-rules":
            self._do_save()
        elif event.button.id == "btn-restore-constraints":
            self.query_one("#input-constraints", TextArea).load_text(DEFAULT_EDITORIAL_CONSTRAINTS)
        else:
            self.dismiss(False)

    def _do_save(self) -> None:
        constraints = self.query_one("#input-constraints", TextArea).text.strip()
        language = self.query_one("#input-override-language", Input).value.strip()
        rules = self.query_one("#input-override-rules", TextArea).text.strip()

        if rules and not language:
            self.notify("Name the language before saving its rules.", severity="warning")
            return

        writing = self._config.writing
        writing.constraints = constraints or DEFAULT_EDITORIAL_CONSTRAINTS
        if self._loaded_language and self._loaded_language != language:
            writing.by_language.pop(self._loaded_language, None)
        if language and rules:
            writing.by_language[language] = rules
        elif language:
            writing.by_language.pop(language, None)

        save_and_sync(self.app, self._directory, self._config, "editorial rules")
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
