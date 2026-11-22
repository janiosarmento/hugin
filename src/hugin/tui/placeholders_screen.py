"""Screens for [img] placeholders: copy their descriptions, keep them off the site."""

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, OptionList, Static
from textual.widgets.option_list import Option


class PlaceholdersScreen(ModalScreen):
    """The post's image placeholders; Enter copies a description for the image generator."""

    BINDINGS = [("escape", "close", "Close")]

    DEFAULT_CSS = """
    PlaceholdersScreen {
        align: center middle;
    }

    #placeholders-modal {
        width: 90%;
        max-width: 110;
        height: auto;
        max-height: 90%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #placeholders-title {
        text-style: bold;
    }

    #placeholders-hint {
        color: $text-muted;
        margin-bottom: 1;
    }

    #placeholders-list {
        height: auto;
        max-height: 30;
    }
    """

    def __init__(self, title: str, descriptions: list[str]) -> None:
        super().__init__()
        self._title = title
        self._descriptions = descriptions

    def compose(self) -> ComposeResult:
        with Vertical(id="placeholders-modal"):
            yield Label(f"Image placeholders in {escape(self._title)}", id="placeholders-title")
            yield Static(
                "Enter copies the description, to paste into ChatGPT or Midjourney. "
                "Put the image where its \\[img] is and delete the placeholder. Esc closes.",
                id="placeholders-hint",
            )
            yield OptionList(
                *(Option(f"{n}. {escape(d)}\n", id=str(n - 1)) for n, d in enumerate(self._descriptions, 1)),
                id="placeholders-list",
            )

    def on_mount(self) -> None:
        self.query_one("#placeholders-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        n = int(event.option.id)
        self.app.copy_to_clipboard(self._descriptions[n])
        self.notify(f"Copied the description of image {n + 1}")

    def action_close(self) -> None:
        self.dismiss(None)


class RemovePlaceholdersScreen(ModalScreen[bool]):
    """Ask before taking placeholders out of published posts; True removes them."""

    BINDINGS = [("y", "yes", "Remove"), ("n", "no", "Cancel"), ("escape", "no", "Cancel")]

    DEFAULT_CSS = """
    RemovePlaceholdersScreen {
        align: center middle;
    }

    #remove-placeholders-modal {
        width: 76;
        height: auto;
        max-height: 90%;
        border: solid $warning;
        background: $surface;
        padding: 1 2;
    }

    #remove-placeholders-modal Label {
        text-style: bold;
        margin-bottom: 1;
    }

    #remove-placeholders-buttons {
        height: auto;
        margin-top: 1;
    }

    #remove-placeholders-buttons Button {
        margin: 0 1;
    }
    """

    MAX_LISTED = 12

    def __init__(self, posts: list[tuple[str, int]]) -> None:
        super().__init__()
        self._posts = posts

    def compose(self) -> ComposeResult:
        shown = self._posts[: self.MAX_LISTED]
        lines = "\n".join(f"  {escape(name)}: {count}" for name, count in shown)
        if len(self._posts) > len(shown):
            lines += f"\n  …and {len(self._posts) - len(shown)} more"
        with Vertical(id="remove-placeholders-modal"):
            yield Label("Published posts still have \\[img] placeholders")
            yield Static(
                f"{lines}\n\nThe site would show them as text. Remove them before syncing? "
                "Drafts keep theirs.",
                id="remove-placeholders-posts",
            )
            with Horizontal(id="remove-placeholders-buttons"):
                yield Button("Remove and sync (y)", id="btn-remove-yes", variant="warning")
                yield Button("Cancel sync (n)", id="btn-remove-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-remove-yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)
