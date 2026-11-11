"""Redirects manager — full screen and supporting modals."""

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button, DataTable, Footer, Input, Label, RadioButton, RadioSet, Static, TextArea,
)

from rich.markup import escape

from hugin.redirects import append_redirect, read_redirects, write_redirects
from hugin.trash import trash_location


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------


class ConfirmDeleteScreen(ModalScreen[bool]):
    """Confirm deleting a post (the file goes to the trash folder)."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    ConfirmDeleteScreen {
        align: center middle;
    }
    #del-modal {
        width: 60;
        height: auto;
        border: solid $error;
        background: $surface;
        padding: 1 2;
    }
    #del-modal Label {
        margin-bottom: 1;
    }
    #del-warning {
        color: $error;
        text-style: bold;
    }
    #del-buttons {
        height: auto;
        margin-top: 1;
    }
    #del-buttons Button {
        margin-right: 1;
    }
    """

    def __init__(self, title: str, url: str) -> None:
        super().__init__()
        self._title = title
        self._url = url

    def compose(self) -> ComposeResult:
        with Vertical(id="del-modal"):
            yield Label(f'Delete "[bold]{self._title}[/bold]"?')
            if self._url:
                yield Label(f"[dim]{self._url}[/dim]")
            yield Label(
                f"The file will be moved to the trash folder, not erased:\n"
                f"{escape(trash_location())}",
                id="del-warning",
            )
            with Horizontal(id="del-buttons"):
                yield Button("Delete", id="btn-delete", variant="error")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-delete")

    def action_cancel(self) -> None:
        self.dismiss(False)


class RedirectSuggestScreen(ModalScreen[str | None]):
    """Offer a redirect after a post is deleted; never required.

    One choice among: no redirect, the post's refactor, similar posts, or a
    URL typed in. "No redirect" is the default unless the post has a
    refactor, which is then preselected. Dismisses with the destination, or
    None for no redirect.
    """

    BINDINGS = [
        ("escape", "skip", "No redirect"),
        ("ctrl+s", "submit", "Confirm"),
    ]

    DEFAULT_CSS = """
    RedirectSuggestScreen {
        align: center middle;
    }
    #suggest-modal {
        width: 80;
        height: auto;
        max-height: 90%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #suggest-body {
        height: auto;
        max-height: 1fr;
    }
    #origin-display {
        color: $accent;
        text-style: bold;
        padding: 0 0 1 0;
    }
    #replacement-note {
        color: $warning;
        margin-bottom: 1;
    }
    #suggest-choices {
        width: 100%;
    }
    #input-dest {
        margin-top: 1;
    }
    #suggest-buttons {
        dock: bottom;
        height: auto;
        margin-top: 1;
    }
    #suggest-buttons Button {
        margin-right: 1;
    }
    """

    NONE = "none"
    OTHER = "other"

    def __init__(self, origin: str, candidates: list[dict],
                 replacement: dict | None = None) -> None:
        """`replacement` ({url, title, draft}) is a refactor of the deleted
        post: it comes first and is preselected."""
        super().__init__()
        self._origin = origin
        self._replacement = replacement
        others = [c for c in candidates if not replacement or c.get("url") != replacement["url"]]
        self._candidates = ([replacement] if replacement else []) + others[:3]

    def compose(self) -> ComposeResult:
        with Vertical(id="suggest-modal"):
            with VerticalScroll(id="suggest-body"):
                yield Label("Post deleted. Add a redirect? (optional)")
                yield Static(f"From: {escape(self._origin)}", id="origin-display")
                if self._replacement:
                    note = (
                        " It is still a draft: the redirect leads nowhere until you publish it."
                        if self._replacement.get("draft") else ""
                    )
                    yield Static(
                        f"Replaced by its refactor \"{escape(self._replacement.get('title', ''))}\", "
                        f"selected below.{note}",
                        id="replacement-note",
                    )
                # The preselected choice comes first, so the cursor starts on it
                choices = [
                    RadioButton(
                        escape(c["url"]) + (f"  [dim]{escape(c['title'])}[/dim]"
                                            if c.get("title") else ""),
                        value=bool(self._replacement) and i == 0, id=f"choice-{i}",
                    )
                    for i, c in enumerate(self._candidates)
                ]
                none = RadioButton("No redirect", value=not self._replacement, id="choice-none")
                choices.insert(1 if self._replacement else 0, none)
                with RadioSet(id="suggest-choices"):
                    yield from choices
                    yield RadioButton("Other URL (type it below)", id="choice-other")
                yield Input(placeholder="/new-destination/", id="input-dest")
            with Horizontal(id="suggest-buttons"):
                yield Button("Confirm (Ctrl+S)", id="btn-add", variant="primary")
                yield Button("No redirect (Esc)", id="btn-skip")

    def on_mount(self) -> None:
        self.query_one("#suggest-choices", RadioSet).focus()

    def _choice(self) -> str:
        pressed = self.query_one("#suggest-choices", RadioSet).pressed_button
        return (pressed.id or "choice-none").removeprefix("choice-") if pressed else self.NONE

    def on_input_changed(self, event: Input.Changed) -> None:
        # Typing a URL picks "Other URL"
        if event.value.strip() and self._choice() != self.OTHER:
            self.query_one("#choice-other", RadioButton).value = True

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-add":
            self.action_submit()
        elif event.button.id == "btn-skip":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_submit()

    def action_submit(self) -> None:
        choice = self._choice()
        if choice == self.NONE:
            self.dismiss(None)
            return
        if choice != self.OTHER:
            self.dismiss(self._candidates[int(choice)]["url"])
            return
        dest = self.query_one("#input-dest", Input).value.strip()
        if not dest:
            self.notify("Type the URL, or choose another option.", severity="warning")
            return
        if not dest.startswith("/") and not dest.startswith("http"):
            dest = "/" + dest
        self.dismiss(dest)

    def action_skip(self) -> None:
        self.dismiss(None)


class AddRedirectScreen(ModalScreen[tuple[str, str] | None]):
    """Modal to add a single redirect entry manually."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    AddRedirectScreen {
        align: center middle;
    }
    #add-modal {
        width: 70;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #add-modal Label {
        margin-top: 1;
    }
    #add-modal Input {
        margin-bottom: 1;
    }
    #add-buttons {
        height: auto;
        margin-top: 1;
    }
    #add-buttons Button {
        margin-right: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="add-modal"):
            yield Label("Add redirect", id="add-title")
            yield Label("Origin:")
            yield Input(placeholder="/old-post/", id="input-origin")
            yield Label("Destination:")
            yield Input(placeholder="/new-post/", id="input-dest")
            with Horizontal(id="add-buttons"):
                yield Button("Add", id="btn-add", variant="primary")
                yield Button("Cancel", id="btn-cancel")

    def on_mount(self) -> None:
        self.query_one("#input-origin", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-add":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "input-origin":
            self.query_one("#input-dest", Input).focus()
        elif event.input.id == "input-dest":
            self._submit()

    def _submit(self) -> None:
        origin = self.query_one("#input-origin", Input).value.strip()
        dest = self.query_one("#input-dest", Input).value.strip()
        if not origin or not dest:
            self.notify("Both fields are required.", severity="warning")
            return
        if not origin.startswith("/"):
            origin = "/" + origin
        if not dest.startswith("/") and not dest.startswith("http"):
            dest = "/" + dest
        self.dismiss((origin, dest))

    def action_cancel(self) -> None:
        self.dismiss(None)


class RawEditScreen(ModalScreen[str | None]):
    """Edit the _redirects file as plain text."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    RawEditScreen {
        align: center middle;
    }
    #raw-modal {
        width: 90%;
        height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #raw-hint {
        color: $text-muted;
        margin-bottom: 1;
    }
    #raw-area {
        height: 1fr;
    }
    #raw-buttons {
        height: auto;
        margin-top: 1;
    }
    #raw-buttons Button {
        margin-right: 1;
    }
    """

    def __init__(self, content: str) -> None:
        super().__init__()
        self._content = content

    def compose(self) -> ComposeResult:
        with Vertical(id="raw-modal"):
            yield Label(
                "One rule per line: /origin /destination 301",
                id="raw-hint",
            )
            yield TextArea(self._content, id="raw-area")
            with Horizontal(id="raw-buttons"):
                yield Button("Save (Ctrl+S)", id="btn-save", variant="primary")
                yield Button("Cancel", id="btn-cancel")

    def on_mount(self) -> None:
        self.query_one("#raw-area", TextArea).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save":
            self.action_save()
        else:
            self.dismiss(None)

    def action_save(self) -> None:
        self.dismiss(self.query_one("#raw-area", TextArea).text)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Main screen
# ---------------------------------------------------------------------------


class RedirectsScreen(Screen[bool]):
    """Browse and manage the _redirects file."""

    BINDINGS = [
        Binding("a", "add", "Add"),
        Binding("delete", "remove", "Remove"),
        Binding("e", "edit_raw", "Edit raw"),
        Binding("escape", "back", "Back"),
    ]

    DEFAULT_CSS = """
    RedirectsScreen {
        background: $surface;
    }
    #rd-header {
        height: auto;
        padding: 1 2;
        background: $panel;
        border-bottom: solid $accent;
    }
    #rd-title {
        text-style: bold;
    }
    #rd-path {
        color: $text-muted;
    }
    #rd-table {
        height: 1fr;
    }
    """

    def __init__(self, redirects_path: Path) -> None:
        super().__init__()
        self._path = redirects_path
        self._changed = False

    def compose(self) -> ComposeResult:
        with Vertical(id="rd-header"):
            yield Label("Redirects", id="rd-title")
            yield Label(str(self._path), id="rd-path")
        yield DataTable(id="rd-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#rd-table", DataTable)
        table.add_column("Origin", key="origin")
        table.add_column("Destination", key="dest")
        table.add_column("Code", key="code", width=6)
        self._reload()
        table.focus()

    def _reload(self) -> None:
        table = self.query_one("#rd-table", DataTable)
        table.clear()
        for i, (origin, dest, code) in enumerate(read_redirects(self._path)):
            table.add_row(origin, dest, code, key=f"r-{i}")

    def action_add(self) -> None:
        def on_result(result: tuple[str, str] | None) -> None:
            if not result:
                return
            origin, dest = result
            append_redirect(self._path, origin, dest)
            self._changed = True
            self._reload()
            self.notify(f"Added: {origin} → {dest}")

        self.app.push_screen(AddRedirectScreen(), on_result)

    def action_remove(self) -> None:
        table = self.query_one("#rd-table", DataTable)
        row = table.cursor_row
        entries = read_redirects(self._path)
        if row is None or row >= len(entries):
            return
        origin, dest, _ = entries[row]
        del entries[row]
        write_redirects(self._path, entries)
        self._changed = True
        self._reload()
        self.notify(f"Removed: {origin} → {dest}")

    def action_edit_raw(self) -> None:
        content = self._path.read_text(encoding="utf-8") if self._path.exists() else ""

        def on_result(new_content: str | None) -> None:
            if new_content is None:
                return
            self._path.write_text(new_content, encoding="utf-8")
            self._changed = True
            self._reload()
            self.notify("Saved.")

        self.app.push_screen(RawEditScreen(content), on_result)

    def action_back(self) -> None:
        self.dismiss(self._changed)
