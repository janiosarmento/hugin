"""Project settings screen."""

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static, TextArea

from hugin.project import DEFAULT_EDITORIAL_CONSTRAINTS, ProjectConfig, save_project


class ProjectSettingsScreen(ModalScreen[bool]):
    """Modal to edit per-project settings."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    ProjectSettingsScreen {
        align: center middle;
    }

    #settings-modal {
        width: 76;
        height: auto;
        max-height: 95%;
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

    #input-constraints {
        height: 12;
    }

    #input-override-rules {
        height: 6;
    }

    #settings-buttons {
        height: auto;
        margin-top: 2;
    }

    #settings-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, config: ProjectConfig, directory: Path, global_words_per_link: int = 300) -> None:
        super().__init__()
        self._config = config
        self._directory = directory
        self._global_wpl = global_words_per_link
        overrides = config.writing.by_language
        # The override shown first; renaming it in the form moves it instead of copying it
        self._loaded_language = next(iter(overrides), "")

    def compose(self) -> ComposeResult:
        writing = self._config.writing
        overrides = ", ".join(writing.by_language) or "none"
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

            yield Label("Editorial constraints", classes="field-label")
            yield Static(
                "Rules sent with every Echo draft of this blog. Blank = Hugin default.",
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

            with Horizontal(id="settings-buttons"):
                yield Button("Save", id="btn-save-settings", variant="primary")
                yield Button("Restore default rules", id="btn-restore-constraints")
                yield Button("Cancel", id="btn-cancel-settings")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save-settings":
            self._do_save()
        elif event.button.id == "btn-restore-constraints":
            self.query_one("#input-constraints", TextArea).load_text(DEFAULT_EDITORIAL_CONSTRAINTS)
        else:
            self.dismiss(False)

    def _do_save(self) -> None:
        words_str = self.query_one("#input-words", Input).value.strip()
        style = self.query_one("#input-style", Input).value.strip()
        wpl_str = self.query_one("#input-words-per-link", Input).value.strip()
        constraints = self.query_one("#input-constraints", TextArea).text.strip()
        language = self.query_one("#input-override-language", Input).value.strip()
        rules = self.query_one("#input-override-rules", TextArea).text.strip()

        if rules and not language:
            self.notify("Name the language before saving its rules.", severity="warning")
            return

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

        writing = self._config.writing
        writing.constraints = constraints or DEFAULT_EDITORIAL_CONSTRAINTS
        if self._loaded_language and self._loaded_language != language:
            writing.by_language.pop(self._loaded_language, None)
        if language and rules:
            writing.by_language[language] = rules
        elif language:
            writing.by_language.pop(language, None)

        save_project(self._directory, self._config)
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
