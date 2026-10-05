"""Echo draft flow: type a prompt, wait for Echo, get a new draft post."""

import asyncio
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, LoadingIndicator, RadioButton, RadioSet, Static, TextArea

from hugin.echo import (
    EchoError,
    build_message,
    create_draft,
    parse_answer,
    _select_parts,
    draw_random,
    pick_category,
    pick_similar,
    select_samples,
    write_with_fallback,
    write_with_system_llm,
)
from hugin.engines import load_fulcrum_echo_persona
from hugin.hugo import load_categories
from hugin.scanner import Post


WAIT_TEXT = "Waiting for {who} (can take several minutes)…  Esc cancels"


WRITER_ECHO = "echo"
WRITER_SYSTEM = "system"

# Remembered for the rest of the session so an A/B run doesn't need re-picking.
_last_writer = WRITER_ECHO


class EchoPromptScreen(ModalScreen[tuple[str, str] | None]):
    """Text area where the user describes the post; also picks who writes it.

    Dismisses with (prompt, writer) where writer is WRITER_ECHO or
    WRITER_SYSTEM, or None when cancelled.
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+s", "submit", "Send"),
        ("f2", "toggle_writer", "Switch writer"),
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
        height: 10;
        margin-bottom: 1;
    }

    #echo-writer {
        layout: horizontal;
        height: auto;
        border: none;
        padding: 0;
        margin-bottom: 1;
    }

    #echo-writer RadioButton {
        margin-right: 3;
    }

    #echo-samples {
        height: auto;
        color: $text-muted;
        margin-bottom: 1;
    }

    #echo-buttons {
        height: auto;
    }

    #echo-buttons Button {
        margin-right: 1;
    }
    """

    DEBOUNCE_SECONDS = 1.0

    def __init__(self, posts: list[Post] | None = None, index=None, engine=None) -> None:
        super().__init__()
        self._index = index
        self._engine = engine
        self._recent, rest = _select_parts(posts or [])
        # Drawn once, so the title shown is the one that gets sent.
        self.random_pick = draw_random(rest)
        self._pool = [p for p in rest if p is not self.random_pick]
        self._similar_note = "2 closest to your prompt (picked when you send)"
        self._similar: list[Post] = []
        self._debounce = None

    def compose(self) -> ComposeResult:
        with Vertical(id="echo-modal"):
            yield Label("Echo — describe the post", id="echo-title")
            yield Static(
                "Echo will get 6 published posts as writing samples (3 latest, "
                "2 similar to your prompt, 1 random). Ctrl+S sends, F2 switches writer, Esc cancels.",
                id="echo-hint",
            )
            yield TextArea(id="echo-prompt")
            with RadioSet(id="echo-writer"):
                yield RadioButton("Echo", id="writer-echo", value=_last_writer == WRITER_ECHO)
                yield RadioButton(self._system_label(), id="writer-system", value=_last_writer == WRITER_SYSTEM)
            yield Static(self._samples_text(), id="echo-samples")
            with Horizontal(id="echo-buttons"):
                yield Button("Send (Ctrl+S)", id="btn-echo-send", variant="primary")
                yield Button("Cancel", id="btn-echo-cancel")

    def on_mount(self) -> None:
        self.query_one("#echo-prompt", TextArea).focus()
        self._fit_prompt()

    def on_resize(self) -> None:
        self._fit_prompt()

    def _fit_prompt(self) -> None:
        # Everything but the text area takes ~20 rows (border, title, hint,
        # samples, buttons); give the text area what is left, between 4 and 10.
        room = int(self.size.height * 0.9) - 21
        self.query_one("#echo-prompt", TextArea).styles.height = max(4, min(10, room))

    def _system_label(self) -> str:
        if self._engine is None:
            return "System LLM"
        return f"{self._engine.id} / {self._engine.model} (direct)"

    def _writer(self) -> str:
        return WRITER_SYSTEM if self.query_one("#writer-system", RadioButton).value else WRITER_ECHO

    def action_toggle_writer(self) -> None:
        target = "#writer-echo" if self._writer() == WRITER_SYSTEM else "#writer-system"
        self.query_one(target, RadioButton).value = True

    @staticmethod
    def _title(post: Post) -> str:
        return str(post.metadata.get("title") or post.path.stem)

    def _samples_text(self) -> str:
        from rich.markup import escape

        def row(tag: str, post: Post) -> str:
            return f"  [b]{tag:<8}[/b] {escape(self._title(post))}"

        lines = [row("latest", p) for p in self._recent]
        if self._similar:
            lines += [row("similar", p) for p in self._similar]
        elif self._pool:
            lines.append(f"  [b]{'similar':<8}[/b] [i]{self._similar_note}[/i]")
        if self.random_pick is not None:
            lines.append(row("random", self.random_pick))
        return "Writing samples sent to Echo:\n" + "\n".join(lines)

    def _refresh_samples(self) -> None:
        self.query_one("#echo-samples", Static).update(self._samples_text())

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        # Re-rank the similar sample once typing pauses.
        if self._debounce is not None:
            self._debounce.stop()
        text = event.text_area.text
        self._debounce = self.set_timer(self.DEBOUNCE_SECONDS, lambda: self._rank(text))

    @work(exclusive=True)
    async def _rank(self, text: str) -> None:
        if self._index is None:
            return
        if not text.strip():
            self._similar = []
            self._similar_note = "2 closest to your prompt (picked when you send)"
            self._refresh_samples()
            return
        try:
            ranked = await asyncio.to_thread(self._index.rank_by_text, text)
        except Exception:
            self._similar = []
            self._similar_note = "random (embeddings unavailable)"
            self._refresh_samples()
            return
        self._similar = pick_similar(self._pool, ranked)
        self._refresh_samples()

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
        global _last_writer
        _last_writer = self._writer()
        self.dismiss((text, _last_writer))

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

    def __init__(self, request: str, posts: list[Post], directory: Path, engine, index=None, random_pick: Post | None = None, writer: str = WRITER_ECHO) -> None:
        super().__init__()
        self._engine = engine
        self._index = index
        self._random_pick = random_pick
        self._writer = writer
        self._request = request
        self._posts = posts
        self._directory = directory

    def compose(self) -> ComposeResult:
        with Vertical(id="echo-wait-modal"):
            yield LoadingIndicator()
            yield Label(self._wait_text(), id="echo-wait-status")

    def _wait_text(self) -> str:
        who = "the system LLM" if self._writer == WRITER_SYSTEM else "Echo"
        return WAIT_TEXT.format(who=who)

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
            self.query_one("#echo-wait-status", Label).update(self._wait_text())
            samples = select_samples(self._posts, ranked, random_pick=self._random_pick)
            if not samples:
                raise EchoError("No published posts to use as writing samples")
            message = build_message(samples, self._request)
            if self._writer == WRITER_SYSTEM:
                text = await write_with_system_llm(message, self._engine)
                who = f"{self._engine.id} / {self._engine.model}"
            else:
                persona = await asyncio.to_thread(load_fulcrum_echo_persona)
                text, echo_error = await write_with_fallback(message, persona, self._engine)
                who = "Echo"
                if echo_error:
                    who = f"{self._engine.id} / {self._engine.model} (Echo failed)"
                    self.notify(
                        f"Echo failed ({echo_error}); draft written by {self._engine.id} instead.",
                        severity="warning",
                        timeout=12,
                    )
            title, body = parse_answer(text, self._request)
            self.query_one("#echo-wait-status", Label).update("Picking a category…")
            categories = await asyncio.to_thread(load_categories, self._directory)
            category = await pick_category(self._engine, title, body, categories)
            path = create_draft(self._directory, text, self._request, category)
            self.notify(f"Written by {who}")
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
