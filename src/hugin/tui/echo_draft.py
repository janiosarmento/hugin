"""Echo draft flow: type a prompt, wait for Echo, get a new draft post."""

import asyncio
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, LoadingIndicator, Static, TextArea

from hugin.echo import (
    EchoError,
    ask_echo,
    build_message,
    create_draft,
    get_api_key,
    parse_answer,
    pick_category,
    select_samples,
)
from hugin.engines import load_fulcrum_echo_persona
from hugin.hugo import load_categories
from hugin.scanner import Post


WAIT_TEXT = "Waiting for Echo (can take several minutes)…  Esc cancels"


class EchoPromptScreen(ModalScreen[str | None]):
    """Text area where the user describes the post Echo should write."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+s", "submit", "Send"),
    ]

    DEFAULT_CSS = """
    EchoPromptScreen {
        align: center middle;
    }

    #echo-modal {
        width: 90%;
        max-width: 110;
        height: auto;
        max-height: 90%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #echo-title {
        text-style: bold;
    }

    #echo-hint {
        color: $text-muted;
        margin-bottom: 1;
    }

    #echo-prompt {
        height: 12;
        margin-bottom: 1;
    }

    #echo-buttons {
        height: auto;
    }

    #echo-buttons Button {
        margin-right: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="echo-modal"):
            yield Label("Echo — describe the post", id="echo-title")
            yield Static(
                "Echo will get 6 published posts as writing samples (3 latest, "
                "2 largest, 1 similar to your prompt). Ctrl+S sends, Esc cancels.",
                id="echo-hint",
            )
            yield TextArea(id="echo-prompt")
            with Horizontal(id="echo-buttons"):
                yield Button("Send (Ctrl+S)", id="btn-echo-send", variant="primary")
                yield Button("Cancel", id="btn-echo-cancel")

    def on_mount(self) -> None:
        self.query_one("#echo-prompt", TextArea).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-echo-send":
            self.action_submit()
        else:
            self.action_cancel()

    def action_submit(self) -> None:
        text = self.query_one("#echo-prompt", TextArea).text.strip()
        if not text:
            self.notify("Write a prompt first.", severity="warning")
            return
        self.dismiss(text)

    def action_cancel(self) -> None:
        self.dismiss(None)


class EchoWaitScreen(ModalScreen[Path | None]):
    """Spinner while Echo writes; dismisses with the draft path (None on failure)."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    EchoWaitScreen {
        align: center middle;
    }

    #echo-wait-modal {
        width: 60;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #echo-wait-status {
        margin-top: 1;
        text-align: center;
    }
    """

    def __init__(self, request: str, posts: list[Post], directory: Path, engine, index=None) -> None:
        super().__init__()
        self._engine = engine
        self._index = index
        self._request = request
        self._posts = posts
        self._directory = directory

    def compose(self) -> ComposeResult:
        with Vertical(id="echo-wait-modal"):
            yield LoadingIndicator()
            yield Label(WAIT_TEXT, id="echo-wait-status")

    def on_mount(self) -> None:
        self._run()

    @work(exclusive=True)
    async def _run(self) -> None:
        try:
            ranked = None
            if self._index is not None:
                self.query_one("#echo-wait-status", Label).update("Finding a similar post…")
                try:
                    ranked = await asyncio.to_thread(self._index.rank_by_text, self._request)
                except Exception:
                    ranked = None  # embeddings unavailable: random sample instead
            self.query_one("#echo-wait-status", Label).update(WAIT_TEXT)
            samples = select_samples(self._posts, ranked)
            if not samples:
                raise EchoError("No published posts to use as writing samples")
            message = build_message(samples, self._request)
            persona = await asyncio.to_thread(load_fulcrum_echo_persona)
            api_key = await asyncio.to_thread(get_api_key)
            text = await ask_echo(message, persona, api_key)
            title, body = parse_answer(text, self._request)
            self.query_one("#echo-wait-status", Label).update("Picking a category…")
            categories = await asyncio.to_thread(load_categories, self._directory)
            category = await pick_category(self._engine, title, body, categories)
            path = create_draft(self._directory, text, self._request, category)
        except EchoError as e:
            self.notify(str(e), severity="error", timeout=10)
            self.dismiss(None)
            return
        except Exception as e:
            self.notify(f"Echo draft failed: {e}", severity="error", timeout=10)
            self.dismiss(None)
            return
        self.dismiss(path)

    def action_cancel(self) -> None:
        self.workers.cancel_all()
        self.notify("Cancelled")
        self.dismiss(None)
