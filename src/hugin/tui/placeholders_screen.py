"""Screens for [img] placeholders: copy their descriptions."""

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList, Static
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
