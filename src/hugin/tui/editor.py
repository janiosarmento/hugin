"""Built-in Markdown editor with frontmatter field editing."""

import re

import frontmatter

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Footer,
    Input,
    Label,
    Static,
    TextArea,
)

from hugin.scanner import Post


_EMOJI_RE = re.compile(
    "[\U0001f000-\U0001ffff"
    "\u2600-\u27bf"
    "\ufe00-\ufe0f"
    "\u200d"
    "]+",
)


# Fields that get dedicated Input widgets (order matters for display)
EDITABLE_FIELDS = ("title", "date", "lastmod", "draft", "slug", "url", "description")
# Fields managed by other tools — show but don't edit here
READONLY_FIELDS = ("tags",)


class ConfirmDiscardScreen(ModalScreen[bool]):
    """Confirm discarding unsaved changes."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    ConfirmDiscardScreen {
        align: center middle;
    }

    #discard-modal {
        width: 50;
        height: auto;
        border: solid $warning;
        background: $surface;
        padding: 1 2;
    }

    #discard-modal Label {
        text-style: bold;
        margin-bottom: 1;
    }

    #discard-buttons {
        height: auto;
        margin-top: 1;
    }

    #discard-buttons Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="discard-modal"):
            yield Label("Discard unsaved changes?")
            yield Static("Your edits will be lost.")
            with Horizontal(id="discard-buttons"):
                yield Button("Discard", id="btn-discard", variant="warning")
                yield Button("Cancel", id="btn-cancel-discard")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-discard")

    def action_cancel(self) -> None:
        self.dismiss(False)


class EditorScreen(Screen[bool]):
    """Full-screen editor for post frontmatter and body."""

    BINDINGS = [
        ("ctrl+s", "save", "Save"),
        ("ctrl+e", "strip_emojis", "Strip emojis"),
        ("escape", "back", "Back"),
    ]

    DEFAULT_CSS = """
    #editor-container {
        height: 1fr;
    }

    #frontmatter-panel {
        height: auto;
        max-height: 40%;
        padding: 1;
        border-bottom: solid $accent;
    }

    .field-row {
        height: auto;
        margin-bottom: 0;
    }

    .field-label {
        width: 15;
        text-style: bold;
        padding-top: 1;
    }

    .field-input {
        width: 1fr;
    }

    .field-readonly {
        width: 1fr;
        color: $text-muted;
        padding-top: 1;
    }

    #body-panel {
        height: 1fr;
        padding: 0 1;
    }

    #body-editor {
        height: 1fr;
    }

    #editor-buttons {
        height: auto;
        padding: 1;
        dock: bottom;
    }

    #editor-buttons Button {
        margin: 0 1;
    }

    #editor-title {
        text-style: bold;
        padding: 1;
        color: $accent;
    }
    """

    def __init__(self, post: Post) -> None:
        super().__init__()
        self.post = post
        self._field_inputs: dict[str, Input] = {}
        self._extra_fields: dict[str, str] = {}
        self._original_meta = dict(post.metadata)
        self._original_content = post.content

    def compose(self) -> ComposeResult:
        yield Static(f"Editing: {self.post.filename}", id="editor-title")

        with Vertical(id="editor-container"):
            with Vertical(id="frontmatter-panel"):
                meta = self.post.metadata

                # Editable fields
                for field_name in EDITABLE_FIELDS:
                    value = meta.get(field_name, "")
                    if value is None:
                        value = ""
                    if isinstance(value, list):
                        value = ", ".join(str(v) for v in value)
                    else:
                        value = str(value) if value else ""

                    with Horizontal(classes="field-row"):
                        yield Label(field_name, classes="field-label")
                        inp = Input(value=value, id=f"field-{field_name}", classes="field-input")
                        self._field_inputs[field_name] = inp
                        yield inp

                # Read-only fields
                for field_name in READONLY_FIELDS:
                    value = meta.get(field_name, "")
                    if isinstance(value, list):
                        display = ", ".join(str(v) for v in value)
                    else:
                        display = str(value) if value else ""

                    with Horizontal(classes="field-row"):
                        yield Label(field_name, classes="field-label")
                        yield Static(f"[dim]{display}[/dim]", classes="field-readonly")

                # Track extra fields not in EDITABLE_FIELDS or READONLY_FIELDS
                known = set(EDITABLE_FIELDS) | set(READONLY_FIELDS)
                for key, value in meta.items():
                    if key not in known:
                        if isinstance(value, list):
                            self._extra_fields[key] = ", ".join(str(v) for v in value)
                        else:
                            self._extra_fields[key] = str(value) if value is not None else ""

            with Vertical(id="body-panel"):
                yield TextArea(
                    self.post.content,
                    language="markdown",
                    id="body-editor",
                )

        with Horizontal(id="editor-buttons"):
            yield Button("Strip emojis (Ctrl+E)", id="btn-strip-emojis")
            yield Button("Save (Ctrl+S)", id="btn-save", variant="primary")
            yield Button("Cancel", id="btn-cancel-edit")

        yield Footer()

    def _is_dirty(self) -> bool:
        """Check if anything has been modified."""
        for field_name, inp in self._field_inputs.items():
            original = self._original_meta.get(field_name, "")
            if original is None:
                original = ""
            if isinstance(original, list):
                original = ", ".join(str(v) for v in original)
            else:
                original = str(original) if original else ""
            if inp.value != original:
                return True

        try:
            body_editor = self.query_one("#body-editor", TextArea)
            if body_editor.text != self._original_content:
                return True
        except Exception:
            pass

        return False

    def action_strip_emojis(self) -> None:
        """Remove all emojis from the body text."""
        editor = self.query_one("#body-editor", TextArea)
        original = editor.text
        cleaned = _EMOJI_RE.sub("", original)
        # Collapse double spaces left behind
        cleaned = re.sub(r"  +", " ", cleaned)
        if cleaned == original:
            self.notify("No emojis found")
            return
        editor.load_text(cleaned)
        self.notify("Emojis removed")

    def action_save(self) -> None:
        """Save the post with updated frontmatter and body."""
        post = self.post

        # Build new metadata starting from original (preserves order and extra fields)
        new_meta = dict(self._original_meta)

        # Update editable fields from inputs
        for field_name, inp in self._field_inputs.items():
            value = inp.value.strip()
            if not value:
                if field_name != "title" and field_name in new_meta:
                    del new_meta[field_name]
                continue

            if field_name == "draft":
                new_meta[field_name] = value.lower() in ("true", "1", "yes")
            elif field_name == "tags":
                new_meta[field_name] = [t.strip() for t in value.split(",") if t.strip()]
            else:
                new_meta[field_name] = value

        # Get body content
        body_editor = self.query_one("#body-editor", TextArea)
        new_content = body_editor.text

        # Build post object and save through centralised writer
        from hugin.writer import save_post

        fm_post = frontmatter.Post(new_content, **new_meta)
        save_post(post.path, fm_post)

        self.notify(f"{post.filename}: saved")
        self.dismiss(True)

    def action_back(self) -> None:
        """Go back, confirming if dirty."""
        if self._is_dirty():
            def on_confirm(discard: bool) -> None:
                if discard:
                    self.dismiss(False)

            self.app.push_screen(ConfirmDiscardScreen(), on_confirm)
        else:
            self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-strip-emojis":
            self.action_strip_emojis()
        elif event.button.id == "btn-save":
            self.action_save()
        elif event.button.id == "btn-cancel-edit":
            self.action_back()
