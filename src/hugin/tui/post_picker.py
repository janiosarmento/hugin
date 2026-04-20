"""Modal screen for manually picking posts to link to."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Static

from hugin.scanner import Post


class PostPickerScreen(ModalScreen[list[dict] | None]):
    """Searchable post picker that returns selected posts as candidates."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    PostPickerScreen {
        align: center middle;
    }

    #picker-modal {
        width: 80;
        height: auto;
        max-height: 85%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }

    #picker-modal Label {
        text-style: bold;
        margin-bottom: 1;
    }

    #picker-search {
        margin-bottom: 1;
    }

    #picker-list {
        height: auto;
        max-height: 25;
        overflow-y: auto;
    }

    #picker-hint {
        margin-top: 1;
        color: $text-muted;
    }

    #picker-buttons {
        height: auto;
        margin-top: 1;
    }

    #picker-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(
        self,
        posts: list[Post],
        current_post: Post,
        url_fn,
    ) -> None:
        super().__init__()
        self._current_path = str(current_post.path.resolve())
        self._url_fn = url_fn
        # Build candidate list sorted by title, excluding current post
        self._candidates: list[dict] = []
        for p in posts:
            if str(p.path.resolve()) == self._current_path:
                continue
            if p.metadata.get("draft"):
                continue
            self._candidates.append({
                "post": p,
                "title": p.metadata.get("title", p.filename),
                "url": url_fn(p.metadata, p.filename),
            })
        self._candidates.sort(key=lambda c: c["title"].lower())
        self._checkboxes: list[tuple[dict, Checkbox]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-modal"):
            yield Label("Pick posts to link to:")
            yield Input(
                placeholder="Type to filter...",
                id="picker-search",
            )
            with Vertical(id="picker-list"):
                for cand in self._candidates:
                    cb = Checkbox(cand["title"])
                    self._checkboxes.append((cand, cb))
                    yield cb
            yield Static(
                "Type to filter, Space to toggle, Enter to confirm",
                id="picker-hint",
            )
            with Horizontal(id="picker-buttons"):
                yield Button("Confirm", id="btn-confirm", variant="primary")
                yield Button("Cancel", id="btn-cancel")

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip().lower()
        for cand, cb in self._checkboxes:
            if not query or query in cand["title"].lower():
                cb.display = True
            else:
                cb.display = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm":
            selected = [
                {"title": cand["title"], "url": cand["url"]}
                for cand, cb in self._checkboxes
                if cb.value
            ]
            if selected:
                self.dismiss(selected)
            else:
                self.notify("No posts selected.", severity="warning")
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
