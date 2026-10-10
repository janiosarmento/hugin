"""Affiliate links editor: browse, add, edit and remove keyword → URL entries."""

import unicodedata

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Input, Label

from hugin.affiliates import (
    AFFILIATES_PATH,
    load_affiliates,
    remove_affiliate,
    save_affiliate,
    validate_affiliate,
)


def _sort_key(keyword: str) -> str:
    decomposed = unicodedata.normalize("NFKD", keyword)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


class AffiliateEditScreen(ModalScreen[tuple[str, str] | None]):
    """Keyword + URL form; dismisses with (keyword, url) or None."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    AffiliateEditScreen {
        align: center middle;
    }
    #aff-modal {
        width: 90%;
        max-width: 100;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #aff-modal Label {
        margin-top: 1;
    }
    #aff-title {
        text-style: bold;
        margin-top: 0;
    }
    #aff-hint {
        color: $text-muted;
    }
    #aff-buttons {
        height: auto;
        margin-top: 1;
    }
    #aff-buttons Button {
        margin-right: 1;
    }
    """

    def __init__(
        self, existing: dict[str, str], keyword: str = "", url: str = ""
    ) -> None:
        super().__init__()
        self._existing = existing
        self._editing = keyword or None
        self._keyword = keyword
        self._url = url

    def compose(self) -> ComposeResult:
        with Vertical(id="aff-modal"):
            yield Label("Edit affiliate link" if self._editing else "Add affiliate link", id="aff-title")
            yield Label("The text that gets linked when it appears in a post (whole word, any case):", id="aff-hint")
            yield Input(self._keyword, placeholder="litter box", id="input-keyword")
            yield Label("Affiliate URL:")
            yield Input(self._url, placeholder="https://amzn.to/...", id="input-url")
            with Horizontal(id="aff-buttons"):
                yield Button("Save", id="btn-save", variant="primary")
                yield Button("Cancel", id="btn-cancel")

    def on_mount(self) -> None:
        self.query_one("#input-keyword", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "input-keyword":
            self.query_one("#input-url", Input).focus()
        else:
            self._submit()

    def _submit(self) -> None:
        keyword = self.query_one("#input-keyword", Input).value.strip()
        url = self.query_one("#input-url", Input).value.strip()
        error = validate_affiliate(keyword, url, self._existing, self._editing)
        if error:
            self.notify(error, severity="warning")
            return
        self.dismiss((keyword, url))

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmRemoveScreen(ModalScreen[bool]):
    """Ask before removing an entry (the file lives outside git)."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    ConfirmRemoveScreen {
        align: center middle;
    }
    #rm-modal {
        width: 70;
        height: auto;
        border: solid $error;
        background: $surface;
        padding: 1 2;
    }
    #rm-buttons {
        height: auto;
        margin-top: 1;
    }
    #rm-buttons Button {
        margin-right: 1;
    }
    """

    def __init__(self, keyword: str, url: str) -> None:
        super().__init__()
        self._keyword = keyword
        self._url = url

    def compose(self) -> ComposeResult:
        with Vertical(id="rm-modal"):
            yield Label(f'Remove "[bold]{self._keyword}[/bold]"?')
            yield Label(f"[dim]{self._url}[/dim]")
            with Horizontal(id="rm-buttons"):
                yield Button("Remove", id="btn-remove", variant="error")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-remove")

    def action_cancel(self) -> None:
        self.dismiss(False)


class AffiliatesScreen(Screen[None]):
    """Manage ~/.hugin/affiliates.toml without touching the file by hand."""

    BINDINGS = [
        Binding("a", "add", "Add"),
        Binding("e", "edit", "Edit"),
        Binding("delete", "remove", "Remove"),
        Binding("backspace", "remove", "Remove", show=False),
        Binding("c", "copy_url", "Copy URL"),
        Binding("escape", "back", "Back"),
    ]

    DEFAULT_CSS = """
    AffiliatesScreen {
        background: $surface;
    }
    #af-header {
        height: auto;
        padding: 1 2;
        background: $panel;
        border-bottom: solid $accent;
    }
    #af-title {
        text-style: bold;
    }
    #af-path {
        color: $text-muted;
    }
    #af-table {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="af-header"):
            yield Label("Affiliate links", id="af-title")
            yield Label(str(AFFILIATES_PATH), id="af-path")
        yield DataTable(id="af-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#af-table", DataTable)
        table.add_column("Keyword", key="keyword")
        table.add_column("URL", key="url")
        self._reload()
        table.focus()

    def _reload(self, select: str | None = None) -> None:
        table = self.query_one("#af-table", DataTable)
        # Alphabetical for browsing (accents and case ignored); the file keeps its own order
        loaded = load_affiliates()
        self._entries = {k: loaded[k] for k in sorted(loaded, key=_sort_key)}
        table.clear()
        for keyword, url in self._entries.items():
            table.add_row(keyword, url, key=keyword)
        if select is not None and select in self._entries:
            table.move_cursor(row=list(self._entries).index(select))

    def _current(self) -> tuple[str, str] | None:
        table = self.query_one("#af-table", DataTable)
        keys = list(self._entries)
        row = table.cursor_row
        if row is None or not 0 <= row < len(keys):
            return None
        return keys[row], self._entries[keys[row]]

    def action_add(self) -> None:
        def on_result(result: tuple[str, str] | None) -> None:
            if not result:
                return
            save_affiliate(*result)
            self._reload(select=result[0])
            self.notify(f"Added: {result[0]}")

        self.app.push_screen(AffiliateEditScreen(self._entries), on_result)

    def action_edit(self) -> None:
        current = self._current()
        if current is None:
            return
        old_keyword, old_url = current

        def on_result(result: tuple[str, str] | None) -> None:
            if not result:
                return
            save_affiliate(result[0], result[1], old_keyword=old_keyword)
            self._reload(select=result[0])
            self.notify(f"Saved: {result[0]}")

        self.app.push_screen(
            AffiliateEditScreen(self._entries, old_keyword, old_url), on_result
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_edit()

    def action_remove(self) -> None:
        current = self._current()
        if current is None:
            return
        keyword, url = current

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                remove_affiliate(keyword)
                self._reload()
                self.notify(f"Removed: {keyword}")

        self.app.push_screen(ConfirmRemoveScreen(keyword, url), on_confirm)

    def action_copy_url(self) -> None:
        current = self._current()
        if current is None:
            return
        self.app.copy_to_clipboard(current[1])
        self.notify(f"Copied URL of {current[0]}")

    def action_back(self) -> None:
        self.dismiss(None)
