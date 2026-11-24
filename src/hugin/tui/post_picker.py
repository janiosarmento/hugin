"""Modal screen for picking posts: to link to (d), or to merge into a refactor (R)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Static

from hugin.normalizer import strip_accents
from hugin.scanner import Post


def matches(query: str, *texts: str) -> bool:
    """Every word of `query` is in one of `texts`, in any order, ignoring case and accents."""
    haystack = " ".join(strip_accents(t) for t in texts)
    return all(word in haystack for word in strip_accents(query).split())


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
        url_fn=None,
        heading: str = "Pick posts to link to:",
        include_drafts: bool = False,
        preselected: set[str] | None = None,
        allow_empty: bool = False,
    ) -> None:
        super().__init__()
        self._current_path = str(current_post.path.resolve())
        self._url_fn = url_fn
        self._heading = heading
        self._preselected = preselected or set()
        # Confirming with nothing checked is an answer (clear the pick), not a mistake
        self._allow_empty = allow_empty
        # Build candidate list sorted by title, excluding current post
        self._candidates: list[dict] = []
        for p in posts:
            if str(p.path.resolve()) == self._current_path:
                continue
            draft = bool(p.metadata.get("draft"))
            if draft and not include_drafts:
                continue
            title = str(p.metadata.get("title", p.filename))
            self._candidates.append({
                "post": p,
                "title": title,
                "label": f"[DRAFT] {title}" if draft else title,
                "url": url_fn(p.metadata, p.filename) if url_fn else "",
            })
        self._candidates.sort(key=lambda c: c["title"].lower())
        self._checkboxes: list[tuple[dict, Checkbox]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-modal"):
            yield Label(self._heading)
            yield Input(
                placeholder="Type to filter...",
                id="picker-search",
            )
            with Vertical(id="picker-list"):
                for cand in self._candidates:
                    cb = Checkbox(
                        cand["label"],
                        value=str(cand["post"].path.resolve()) in self._preselected,
                    )
                    self._checkboxes.append((cand, cb))
                    yield cb
            yield Static(self._hint(), id="picker-hint")
            with Horizontal(id="picker-buttons"):
                yield Button("Confirm", id="btn-confirm", variant="primary")
                yield Button("Cancel", id="btn-cancel")

    def _hint(self) -> str:
        chosen = sum(cb.value for _, cb in self._checkboxes)
        shown = sum(bool(cb.display) for _, cb in self._checkboxes)
        return (
            f"{chosen} selected · {shown} of {len(self._checkboxes)} shown. "
            "Type words of the title or file name (any order, accents optional); "
            "Space toggles, Enter in the filter confirms"
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip()
        for cand, cb in self._checkboxes:
            # Checked posts stay in view, so a new filter never hides what was picked
            cb.display = cb.value or matches(query, cand["title"], cand["post"].filename)
        self.query_one("#picker-hint", Static).update(self._hint())

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self.query_one("#picker-hint", Static).update(self._hint())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._confirm()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm":
            self._confirm()
        else:
            self.dismiss(None)

    def _confirm(self) -> None:
        selected = [
            {"title": cand["title"], "url": cand["url"], "path": str(cand["post"].path.resolve())}
            for cand, cb in self._checkboxes
            if cb.value
        ]
        if selected or self._allow_empty:
            self.dismiss(selected)
        else:
            self.notify("No posts selected.", severity="warning")

    def action_cancel(self) -> None:
        self.dismiss(None)
