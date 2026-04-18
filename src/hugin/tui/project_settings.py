"""Project settings screen."""

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from hugin.project import ProjectConfig, save_project


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

    def __init__(self, config: ProjectConfig, directory: Path, global_words_per_link: int = 300) -> None:
        super().__init__()
        self._config = config
        self._directory = directory
        self._global_wpl = global_words_per_link

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-modal"):
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

            with Horizontal(id="settings-buttons"):
                yield Button("Save", id="btn-save-settings", variant="primary")
                yield Button("Cancel", id="btn-cancel-settings")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save-settings":
            self._do_save()
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

        save_project(self._directory, self._config)
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
