"""Project settings screen, and the editorial rules, author profiles and prompts screens it opens."""

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static
from hugin.tui.form import titled
from hugin.tui.widgets import TextArea

from hugin import prompts
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

            yield titled("Summary words", Input(
                value=str(self._config.summary.words),
                id="input-words",
                type="integer",
            ))
            yield Static("Target word count for generated summaries", classes="field-hint")

            yield titled("Summary style", Input(
                value=self._config.summary.style,
                id="input-style",
            ))
            yield Static("Tone instruction appended to the summary prompt", classes="field-hint")

            yield titled("Words per link", Input(
                value=str(self._config.links.words_per_link),
                id="input-words-per-link",
                type="integer",
            ))
            yield Static(f"1 link per N words (0 = use global default: {self._global_wpl})", classes="field-hint")

            yield Label("Editorial rules", classes="field-label")
            yield Static(
                "Guidelines sent with every LLM draft of this blog, and per-language overrides.",
                classes="field-hint",
            )
            yield Button("Editorial rules…", id="btn-editorial-rules")

            yield Label("Author profiles", classes="field-label")
            yield Static(
                "Voice and style of each author, sent with their LLM drafts. Not the public author bio.",
                classes="field-hint",
            )
            yield Button("Author profiles…", id="btn-author-profiles")

            yield Label("Prompts", classes="field-label")
            changed = len(self._config.prompts)
            yield Static(
                "Every prompt Hugin sends to the LLM, editable for this blog"
                + (f" ({changed} changed)." if changed else "."),
                classes="field-hint",
            )
            yield Button("Prompts…", id="btn-prompts")

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
        elif event.button.id == "btn-prompts":
            self.app.push_screen(PromptsScreen(self._config, self._directory))
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

    #profiles-modal .field-hint {
        color: $text-muted;
    }

    #profiles-modal Field.narrow {
        width: 50;
    }

    #profiles-modal Field.grow {
        height: 1fr;
    }

    #input-profile-text {
        height: 1fr;
        min-height: 2;
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

            yield titled("Author", Select(
                [(a, a) for a in self._authors],
                value=self._loaded or Select.NULL,
                allow_blank=True,
                id="select-profile-author",
            ), classes="narrow")
            yield titled(
                "Author name",
                Input(value=self._loaded, placeholder="Author name", id="input-profile-name"),
                classes="narrow",
            )
            yield Static(
                "Pick an author to edit, or leave blank to add a new one. "
                "The name must match the author front matter of the posts.",
                classes="field-hint",
            )

            yield titled(
                "Profile",
                TextArea(self._profiles.get(self._loaded, ""), id="input-profile-text"),
                classes="grow",
            )
            yield Static(
                "Voice and style to write in, e.g. formal and academic, or warm and informal. "
                "Blank removes this author's profile.",
                classes="field-hint",
            )

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

    #rules-modal .field-hint {
        color: $text-muted;
    }

    #rules-modal Field.grow {
        height: 1fr;
    }

    #input-constraints {
        height: 1fr;
        min-height: 1;
    }

    #input-override-rules {
        height: 2;
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

            yield titled(
                "Blog rules", TextArea(writing.constraints, id="input-constraints"), classes="grow",
            )
            yield Static(
                "Sent with every LLM draft of this blog. Blank = Hugin default.",
                classes="field-hint",
            )

            yield titled(
                "Language override",
                Input(value=self._loaded_language, id="input-override-language"),
            )
            yield titled("Override rules", TextArea(
                writing.by_language.get(self._loaded_language, ""),
                id="input-override-rules",
            ))
            yield Static(
                "Language as detected from the posts (Portuguese, English, Spanish, French). "
                "Blank rules remove the override. Current overrides: " + overrides,
                classes="field-hint",
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


class PromptsScreen(ModalScreen[bool]):
    """Edit the LLM prompts for this blog; saves straight to the project file.

    Edits are kept while switching prompts and saved together. Only prompts
    that differ from the default are written, so "Restore default" (or
    pasting the default back) drops the override.
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    PromptsScreen {
        align: center middle;
    }

    #prompts-modal {
        width: 95%;
        max-width: 130;
        height: 100%;
        max-height: 100%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #prompts-modal .settings-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #prompts-modal .field-hint {
        color: $text-muted;
    }

    #prompts-modal Field.grow {
        height: 1fr;
    }

    #input-prompt-text {
        height: 1fr;
        min-height: 3;
    }

    #prompts-buttons {
        height: auto;
        margin-top: 1;
    }

    #prompts-buttons Button {
        margin: 0 1 0 0;
    }
    """

    def __init__(self, config: ProjectConfig, directory: Path) -> None:
        super().__init__()
        self._config = config
        self._directory = directory
        # Text of every prompt as the form holds it: saved overrides, then edits
        self._texts = {key: prompts.text_of(key, config.prompts) for key in prompts.PROMPTS}
        self._loaded = next(iter(prompts.PROMPTS))

    def compose(self) -> ComposeResult:
        with Vertical(id="prompts-modal"):
            yield Label("Prompts", classes="settings-title")
            yield titled("Prompt", Select(
                [(self._option_label(key), key) for key in prompts.PROMPTS],
                value=self._loaded,
                allow_blank=False,
                id="select-prompt",
            ))
            yield Static(self._hint(), id="prompt-hint", classes="field-hint")
            yield titled(
                "Text", TextArea(self._texts[self._loaded], id="input-prompt-text"), classes="grow",
            )
            with Horizontal(id="prompts-buttons"):
                yield Button("Save", id="btn-save-prompts", variant="primary")
                yield Button("Restore default", id="btn-restore-prompt")
                yield Button("Cancel", id="btn-cancel-prompts")

    def _option_label(self, key: str) -> str:
        changed = key in self._config.prompts
        return prompts.PROMPTS[key].label + (" (changed)" if changed else "")

    def _hint(self) -> str:
        spec = prompts.PROMPTS[self._loaded]
        state = "Changed for this blog" if key_changed(self._loaded, self._texts) else "Hugin default"
        if spec.fields is None:
            return f"{state}. Sent as written, no placeholders."
        fields = ", ".join("{" + f + "}" for f in spec.fields) or "none"
        return (
            f"{state}. Placeholders filled by Hugin: {fields}. "
            "Write a literal brace doubled: {{ or }}."
        )

    def _keep_edit(self) -> None:
        self._texts[self._loaded] = self.query_one("#input-prompt-text", TextArea).text

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "select-prompt" or event.value in (Select.NULL, self._loaded):
            return
        self._keep_edit()
        self._loaded = event.value
        self.query_one("#input-prompt-text", TextArea).load_text(self._texts[self._loaded])
        self.query_one("#prompt-hint", Static).update(self._hint())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save-prompts":
            self._do_save()
        elif event.button.id == "btn-restore-prompt":
            self.query_one("#input-prompt-text", TextArea).load_text(prompts.PROMPTS[self._loaded].default)
            self._keep_edit()
            self.query_one("#prompt-hint", Static).update(self._hint())
        else:
            self.dismiss(False)

    def _do_save(self) -> None:
        self._keep_edit()
        overrides = prompts.only_changed(self._texts)
        missing = []
        for key, text in overrides.items():
            error, left_out = prompts.check(key, text)
            if error:
                self._show(key)
                self.notify(f"{prompts.PROMPTS[key].label}: {error}", severity="error", timeout=10)
                return
            if left_out:
                names = ", ".join("{" + f + "}" for f in left_out)
                missing.append(f"{prompts.PROMPTS[key].label} leaves out {names}")

        self._config.prompts = overrides
        save_and_sync(self.app, self._directory, self._config, "prompts")
        for line in missing:
            self.app.notify(line, severity="warning", timeout=10)
        self.dismiss(True)

    def _show(self, key: str) -> None:
        """Bring `key` into the form (to point at its problem)."""
        self.query_one("#select-prompt", Select).value = key
        self._loaded = key
        self.query_one("#input-prompt-text", TextArea).load_text(self._texts[key])
        self.query_one("#prompt-hint", Static).update(self._hint())

    def action_cancel(self) -> None:
        self.dismiss(False)


def key_changed(key: str, texts: dict[str, str]) -> bool:
    return key in prompts.only_changed({key: texts.get(key, "")})
