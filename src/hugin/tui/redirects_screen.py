"""Redirects manager — full screen and supporting modals."""

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Input, Label, Static, TextArea

from hugin.redirects import append_redirect, read_redirects, write_redirects


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------


class ConfirmDeleteScreen(ModalScreen[bool]):
    """Confirm permanent deletion of a post."""

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
            yield Label("This cannot be undone (use git to recover).", id="del-warning")
            with Horizontal(id="del-buttons"):
                yield Button("Delete", id="btn-delete", variant="error")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-delete")

    def action_cancel(self) -> None:
        self.dismiss(False)


class RedirectSuggestScreen(ModalScreen[str | None]):
    """Offer a redirect destination after a post is deleted."""

    BINDINGS = [("escape", "skip", "Skip")]

    DEFAULT_CSS = """
    RedirectSuggestScreen {
        align: center middle;
    }
    #suggest-modal {
        width: 72;
        height: auto;
        max-height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #suggest-modal Label {
        margin-top: 1;
    }
    #origin-display {
        color: $accent;
        text-style: bold;
        padding: 0 0 1 0;
    }
    .candidate-btn {
        width: 100%;
        margin-bottom: 0;
    }
    #input-dest {
        margin-top: 1;
        margin-bottom: 1;
    }
    #suggest-buttons {
        height: auto;
        margin-top: 1;
    }
    #suggest-buttons Button {
        margin-right: 1;
    }
    """

    def __init__(self, origin: str, candidates: list[dict]) -> None:
        super().__init__()
        self._origin = origin
        self._candidates = candidates[:3]

    def compose(self) -> ComposeResult:
        with Vertical(id="suggest-modal"):
            yield Label("Post deleted. Add a redirect?")
            yield Static(f"From: {self._origin}", id="origin-display")
            if self._candidates:
                yield Label("Suggested destinations (click to select):")
                for i, c in enumerate(self._candidates):
                    url = c["url"]
                    title = c.get("title", "")
                    label = f"{url}" + (f"  [dim]{title}[/dim]" if title else "")
                    yield Button(label, id=f"cand-{i}", classes="candidate-btn")
            yield Label("Destination URL:")
            yield Input(placeholder="/new-destination/", id="input-dest")
            with Horizontal(id="suggest-buttons"):
                yield Button("Add redirect", id="btn-add", variant="primary")
                yield Button("Skip", id="btn-skip")

    def on_mount(self) -> None:
        self.query_one("#input-dest", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "btn-add":
            self._submit()
        elif bid == "btn-skip":
            self.dismiss(None)
        elif bid.startswith("cand-"):
            idx = int(bid[5:])
            if idx < len(self._candidates):
                self.query_one("#input-dest", Input).value = self._candidates[idx]["url"]

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        dest = self.query_one("#input-dest", Input).value.strip()
        if not dest:
            self.notify("Enter a destination URL.", severity="warning")
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
