"""Broken internal links screen — find and remove links to draft/missing posts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label, Static

from hugin.linker import (
    BrokenLink,
    find_broken_links,
    remove_specific_links,
    write_post_with_links,
)

if TYPE_CHECKING:
    from hugin.hugo import HugoSite
    from hugin.scanner import Post


class BrokenLinksScreen(ModalScreen[bool]):
    """Modal screen to find and remove broken internal links."""

    BINDINGS = [
        ("escape", "close", "Close"),
    ]

    DEFAULT_CSS = """
    BrokenLinksScreen {
        align: center middle;
    }

    #broken-links-modal {
        width: 90%;
        height: 80%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }

    #broken-links-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #broken-links-table {
        height: 1fr;
    }

    #broken-links-empty {
        width: 100%;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }

    #broken-links-buttons {
        height: auto;
        margin-top: 1;
    }

    #broken-links-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, all_posts: list[Post], site: HugoSite) -> None:
        super().__init__()
        self.all_posts = all_posts
        self.site = site
        self._broken: list[BrokenLink] = []
        self._selected: set[int] = set()
        self._changed = False

    def compose(self) -> ComposeResult:
        with Vertical(id="broken-links-modal"):
            yield Label("", id="broken-links-title")
            yield DataTable(
                id="broken-links-table",
                cursor_type="row",
                zebra_stripes=True,
            )
            yield Static(
                "No broken internal links found.",
                id="broken-links-empty",
                classes="hidden",
            )
            with Horizontal(id="broken-links-buttons"):
                yield Button(
                    "Remove selected",
                    id="btn-remove",
                    variant="error",
                )
                yield Button("Close", id="btn-close")

    def on_mount(self) -> None:
        self._broken = find_broken_links(self.all_posts, self.site)
        self._refresh()

    def _refresh(self) -> None:
        title = self.query_one("#broken-links-title", Label)
        table = self.query_one("#broken-links-table", DataTable)
        empty = self.query_one("#broken-links-empty", Static)
        btn_remove = self.query_one("#btn-remove", Button)

        table.clear(columns=True)
        self._selected.clear()

        if not self._broken:
            title.update("Broken internal links")
            table.add_class("hidden")
            empty.remove_class("hidden")
            btn_remove.add_class("hidden")
            return

        title.update(f"Broken internal links ({len(self._broken)})")
        table.remove_class("hidden")
        empty.add_class("hidden")
        btn_remove.remove_class("hidden")

        table.add_column("✓", key="selected", width=3)
        table.add_column("Source", key="source")
        table.add_column("Anchor", key="anchor")
        table.add_column("Target", key="target")
        table.add_column("Reason", key="reason")

        for i, bl in enumerate(self._broken):
            source_title = bl.source_post.metadata.get(
                "title", bl.source_post.filename,
            )
            reason_label = "draft" if bl.reason == "draft" else "not found"
            table.add_row(
                " ",
                source_title,
                bl.anchor_text,
                bl.target_url,
                reason_label,
                key=f"bl-{i}",
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_index = event.cursor_row
        if row_index is None:
            return
        table = self.query_one("#broken-links-table", DataTable)
        key = f"bl-{row_index}"
        if row_index in self._selected:
            self._selected.discard(row_index)
            table.update_cell(key, "selected", " ")
        else:
            self._selected.add(row_index)
            table.update_cell(key, "selected", "✓")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-remove":
            self._do_remove()
        elif event.button.id == "btn-close":
            self.dismiss(self._changed)

    def _do_remove(self) -> None:
        if not self._selected:
            self.notify("No links selected.", severity="warning")
            return

        # Group selected broken links by source post path
        by_post: dict[str, set[str]] = {}
        for idx in self._selected:
            bl = self._broken[idx]
            key = str(bl.source_post.path)
            if key not in by_post:
                by_post[key] = set()
            by_post[key].add(bl.target_url)

        total_removed = 0
        for idx in self._selected:
            bl = self._broken[idx]
            post_key = str(bl.source_post.path)
            if post_key not in by_post:
                continue

            urls = by_post.pop(post_key)
            post = bl.source_post
            body, removed = remove_specific_links(post.content, urls)
            write_post_with_links(post.path, body)
            post.content = body
            total_removed += removed

        self._changed = True

        # Re-scan after removal
        self._broken = find_broken_links(self.all_posts, self.site)
        self._refresh()
        self.notify(f"{total_removed} broken links removed")

    def action_close(self) -> None:
        self.dismiss(self._changed)
