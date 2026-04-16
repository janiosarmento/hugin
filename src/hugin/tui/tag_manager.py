"""Tag management screen — audit, delete, merge, rename tags across all posts."""

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
)

from hugin.normalizer import normalize_tag, sort_key
from hugin.scanner import Post
from hugin.writer import write_tags


class MergeSourcesScreen(ModalScreen[list[str] | None]):
    """Modal to select which tags to merge into the target."""

    BINDINGS = [
        ("up", "focus_prev", ""),
        ("down", "focus_next", ""),
        ("pageup", "page_up", ""),
        ("pagedown", "page_down", ""),
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    MergeSourcesScreen {
        align: center middle;
    }

    #merge-modal {
        width: 60;
        height: auto;
        max-height: 80%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }

    #merge-modal Label {
        text-style: bold;
        margin-bottom: 1;
    }

    #merge-checkboxes {
        height: auto;
        max-height: 20;
        overflow-y: auto;
    }

    #merge-hint {
        margin-top: 1;
        color: $text-muted;
    }

    #merge-buttons {
        height: auto;
        margin-top: 1;
    }
    """

    def __init__(self, tags: list[tuple[str, int]], target_tag: str) -> None:
        super().__init__()
        self.tags = sorted(
            [(t, c) for t, c in tags if t != target_tag],
            key=lambda x: sort_key(x[0]),
        )
        self.target_tag = target_tag
        self._checkboxes: list[tuple[str, Checkbox]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="merge-modal"):
            yield Label(f"Merge into '{self.target_tag}' — select tags to merge:")
            with Vertical(id="merge-checkboxes"):
                for tag, count in self.tags:
                    cb = Checkbox(f"{tag} ({count})")
                    self._checkboxes.append((tag, cb))
                    yield cb
            yield Static("Space to toggle, Enter to confirm", id="merge-hint")
            with Horizontal(id="merge-buttons"):
                yield Button("Merge selected", id="btn-merge", variant="primary")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-merge":
            selected = [tag for tag, cb in self._checkboxes if cb.value]
            if selected:
                self.dismiss(selected)
            else:
                self.notify("No tags selected.", severity="warning")
        else:
            self.dismiss(None)

    def action_focus_prev(self) -> None:
        self.focus_previous()

    def action_focus_next(self) -> None:
        self.focus_next()

    def _focused_cb_index(self) -> int | None:
        focused = self.focused
        for i, (_, cb) in enumerate(self._checkboxes):
            if cb is focused:
                return i
        return None

    def action_page_up(self) -> None:
        idx = self._focused_cb_index()
        if idx is None:
            return
        target = max(0, idx - 10)
        self._checkboxes[target][1].focus()

    def action_page_down(self) -> None:
        idx = self._focused_cb_index()
        if idx is None:
            return
        target = min(len(self._checkboxes) - 1, idx + 10)
        self._checkboxes[target][1].focus()

    def action_cancel(self) -> None:
        self.dismiss(None)


class RenameScreen(ModalScreen[str | None]):
    """Modal to rename a tag."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    RenameScreen {
        align: center middle;
    }

    #rename-modal {
        width: 50;
        height: auto;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }

    #rename-modal Label {
        text-style: bold;
        margin-bottom: 1;
    }

    #rename-buttons {
        height: auto;
        margin-top: 1;
    }
    """

    def __init__(self, tag: str) -> None:
        super().__init__()
        self.tag = tag

    def compose(self) -> ComposeResult:
        with Vertical(id="rename-modal"):
            yield Label(f"Rename '{self.tag}' to:")
            yield Input(value=self.tag, id="rename-input")
            with Horizontal(id="rename-buttons"):
                yield Button("Rename", id="btn-confirm", variant="primary")
                yield Button("Cancel", id="btn-cancel")

    def on_mount(self) -> None:
        self.query_one("#rename-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        new_name = normalize_tag(self.query_one("#rename-input", Input).value)
        if new_name and new_name != self.tag:
            self.dismiss(new_name)
        elif new_name == self.tag:
            self.dismiss(None)
        else:
            self.notify("Tag name cannot be empty", severity="warning")

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmDeleteScreen(ModalScreen[bool]):
    """Confirmation modal for tag deletion."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    ConfirmDeleteScreen {
        align: center middle;
    }

    #confirm-modal {
        width: 50;
        height: auto;
        border: solid $error;
        background: $surface;
        padding: 1 2;
    }

    #confirm-modal Label {
        text-style: bold;
        margin-bottom: 1;
    }

    #confirm-buttons {
        height: auto;
        margin-top: 1;
    }

    #confirm-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, tag: str, count: int) -> None:
        super().__init__()
        self.tag = tag
        self.count = count

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-modal"):
            yield Label(f"Delete '{self.tag}' from {self.count} posts?")
            with Horizontal(id="confirm-buttons"):
                yield Button("Delete", id="btn-confirm", variant="error")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


class TagManagerScreen(Screen):
    """Screen to audit, delete, merge, and rename tags across all posts."""

    BINDINGS = [
        ("q", "go_back", "Quit"),
        ("m", "merge", "Merge"),
        ("r", "rename", "Rename"),
        ("d", "delete", "Delete"),
        ("delete", "delete", ""),
        ("backspace", "delete", ""),
        ("escape", "go_back", ""),
    ]

    DEFAULT_CSS = """
    #tag-manager-container {
        height: 1fr;
        padding: 1;
    }

    #tag-manager-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #tag-table {
        height: 1fr;
    }

    #tag-stats {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        all_posts: list[Post],
        pool: dict[str, int],
        directory: Path,
    ) -> None:
        super().__init__()
        self.all_posts = all_posts
        self.pool = pool
        self.directory = directory

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="tag-manager-container"):
            yield Label("Tag Manager", id="tag-manager-title")
            yield Static("", id="tag-stats")
            yield DataTable(id="tag-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#tag-table", DataTable)
        table.add_column("Tag", key="tag")
        table.add_column("Posts", key="count", width=6)
        self._refresh_table()

    def _sorted_tags(self) -> list[tuple[str, int]]:
        return sorted(self.pool.items(), key=lambda x: (-x[1], sort_key(x[0])))

    def _selected_tag(self) -> tuple[str, int] | None:
        tags = self._sorted_tags()
        table = self.query_one("#tag-table", DataTable)
        index = table.cursor_row
        if index is not None and 0 <= index < len(tags):
            return tags[index]
        return None

    def _refresh_table(self) -> None:
        table = self.query_one("#tag-table", DataTable)
        table.clear()

        tags = self._sorted_tags()
        for i, (tag, count) in enumerate(tags):
            table.add_row(tag, str(count), key=f"tag-{i}")

        stats = self.query_one("#tag-stats", Static)
        stats.update(f"{len(tags)} unique tags across {len(self.all_posts)} posts")

    # --- Actions ---

    def action_merge(self) -> None:
        selected = self._selected_tag()
        if not selected:
            return
        target_tag, _ = selected
        self.app.push_screen(
            MergeSourcesScreen(self._sorted_tags(), target_tag),
            lambda sources: self._do_merge_multi(sources, target_tag),
        )

    def action_rename(self) -> None:
        selected = self._selected_tag()
        if not selected:
            return
        tag, _ = selected
        self.app.push_screen(
            RenameScreen(tag),
            lambda new_name: self._do_rename(new_name, tag),
        )

    def action_delete(self) -> None:
        selected = self._selected_tag()
        if not selected:
            return
        tag, count = selected
        self.app.push_screen(
            ConfirmDeleteScreen(tag, count),
            lambda confirmed: self._do_delete(tag) if confirmed else None,
        )

    # --- Operations ---

    def _do_merge_multi(self, sources: list[str] | None, target: str) -> None:
        if not sources:
            return
        affected = 0
        for source in sources:
            for post in self.all_posts:
                if source in post.tags:
                    post.tags.remove(source)
                    if target not in post.tags:
                        post.tags.append(target)
                    post.metadata["tags"] = post.tags
                    write_tags(post.path, post.tags)
                    affected += 1

            source_count = self.pool.pop(source, 0)
            self.pool[target] = self.pool.get(target, 0) + source_count

        self._refresh_table()
        tags_list = ", ".join(sources)
        self.notify(f"Merged {tags_list} → '{target}' ({affected} posts)")

    def _do_rename(self, new_name: str | None, old_name: str) -> None:
        if new_name is None:
            return
        affected = 0
        for post in self.all_posts:
            if old_name in post.tags:
                idx = post.tags.index(old_name)
                if new_name not in post.tags:
                    post.tags[idx] = new_name
                else:
                    post.tags.pop(idx)
                post.metadata["tags"] = post.tags
                write_tags(post.path, post.tags)
                affected += 1

        count = self.pool.pop(old_name, 0)
        self.pool[new_name] = self.pool.get(new_name, 0) + count
        self._refresh_table()
        self.notify(f"Renamed '{old_name}' → '{new_name}' ({affected} posts)")

    def _do_delete(self, tag: str) -> None:
        affected = 0
        for post in self.all_posts:
            if tag in post.tags:
                post.tags.remove(tag)
                post.metadata["tags"] = post.tags
                write_tags(post.path, post.tags)
                affected += 1

        self.pool.pop(tag, None)
        self._refresh_table()
        self.notify(f"Deleted '{tag}' from {affected} posts")

    def action_go_back(self) -> None:
        self.dismiss(None)
