"""Echo draft flow: type a prompt, wait for Echo, get a new draft post."""

import asyncio
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, LoadingIndicator, RadioButton, RadioSet, Static, TextArea

from hugin.echo import (
    MIN_SAMPLES,
    N_RANDOM,
    N_RECENT,
    N_SIMILAR,
    EMBEDDING_ERRORS,
    R_N_LIKE_ORIGINAL,
    R_N_RECENT,
    R_N_SIMILAR,
    EchoError,
    build_message,
    create_draft,
    parse_answer,
    _select_parts,
    draw_random,
    inherited_fields,
    original_query,
    pick_category,
    pick_similar,
    select_refactor_samples,
    select_samples,
    write_with_fallback,
    write_with_system_llm,
)
from hugin.engines import load_fulcrum_echo_persona
from hugin.log import log_exception
from hugin.hugo import load_categories
from hugin.normalizer import detect_language
from hugin.project import load_project
from hugin.scanner import Post


WAIT_TEXT = "Waiting for {who} (can take several minutes)…"


SIMILAR_PENDING = f"{N_SIMILAR} closest to your prompt (picked when you send)"
R_SIMILAR_PENDING = f"{R_N_SIMILAR} closest to your prompt (picked when you send)"

WRITER_ECHO = "echo"
WRITER_SYSTEM = "system"

# Remembered for the rest of the session so an A/B run doesn't need re-picking.
_last_writer = WRITER_ECHO


class EchoPromptScreen(ModalScreen[tuple[str, str] | None]):
    """Text area where the user describes the post; also picks who writes it.

    Dismisses with (prompt, writer) where writer is WRITER_ECHO or
    WRITER_SYSTEM, or None when cancelled. With `original` it is the refactor
    variant: the samples are 2 latest, 2 closest to the prompt and 2 closest
    to that post.
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

    def __init__(
        self, posts: list[Post] | None = None, index=None, engine=None,
        original: Post | None = None,
    ) -> None:
        super().__init__()
        self._index = index
        self._engine = engine
        self._original = original
        self._recent, rest = _select_parts(posts or [], original)
        # Drawn once, so the title shown is the one that gets sent.
        self.random_pick = None if original else draw_random(rest)
        self._pool = [p for p in rest if p is not self.random_pick]
        self._similar_note = R_SIMILAR_PENDING if original else SIMILAR_PENDING
        self._similar: list[Post] = []
        self._like_original: list[Post] = []
        self._ranked_prompt: list[str] | None = None
        self._ranked_original: list[str] | None = None
        self._debounce = None

    def compose(self) -> ComposeResult:
        with Vertical(id="echo-modal"):
            if self._original is None:
                heading = "Echo — describe the post"
                mix = f"{N_RECENT} latest, {N_SIMILAR} similar to your prompt"
                if N_RANDOM:
                    mix += f", {N_RANDOM} random"
                count = MIN_SAMPLES
            else:
                heading = f"Refactor — {self._title(self._original)}"
                mix = (
                    f"{R_N_RECENT} latest, {R_N_SIMILAR} similar to your prompt, "
                    f"{R_N_LIKE_ORIGINAL} similar to the original"
                )
                count = R_N_RECENT + R_N_SIMILAR + R_N_LIKE_ORIGINAL
            yield Label(heading, id="echo-title")
            refactor_note = (
                "The post is rewritten in full to replace the original (not a follow-up): "
                "say what to change or improve. "
                if self._original is not None else ""
            )
            yield Static(
                f"{refactor_note}{count} published posts go along as writing samples ({mix}). "
                "Ctrl+S sends, F2 switches writer, Esc cancels.",
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
        if self._original is not None:
            self._rank_original()

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
        if self._original is not None:
            if self._like_original:
                lines += [row("like it", p) for p in self._like_original]
            elif self._pool:
                lines.append(
                    f"  [b]{'like it':<8}[/b] [i]{R_N_LIKE_ORIGINAL} closest to the original[/i]"
                )
        if self.random_pick is not None:
            lines.append(row("random", self.random_pick))
        return "Writing samples sent along:\n" + "\n".join(lines)

    def _refresh_samples(self) -> None:
        self.query_one("#echo-samples", Static).update(self._samples_text())

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        # Re-rank the similar sample once typing pauses.
        if self._debounce is not None:
            self._debounce.stop()
        text = event.text_area.text
        self._debounce = self.set_timer(self.DEBOUNCE_SECONDS, lambda: self._rank(text))

    @work(exclusive=True, group="rank-prompt")
    async def _rank(self, text: str) -> None:
        if self._index is None:
            return
        if not text.strip():
            self._ranked_prompt = None
            self._similar = []
            self._similar_note = R_SIMILAR_PENDING if self._original else SIMILAR_PENDING
            self._pick_displayed()
            return
        try:
            self._ranked_prompt = await asyncio.to_thread(self._index.rank_by_text, text)
        except EMBEDDING_ERRORS:
            log_exception("echo: rank prompt")
            self._ranked_prompt = None
            self._similar = []
            self._similar_note = "random (embeddings unavailable)"
            self._refresh_samples()
            return
        self._pick_displayed()

    @work(exclusive=True, group="rank-original")
    async def _rank_original(self) -> None:
        if self._index is None:
            return
        try:
            self._ranked_original = await asyncio.to_thread(
                self._index.rank_by_text, original_query(self._original)
            )
        except EMBEDDING_ERRORS:
            log_exception("echo: rank original")
            return  # the wait screen falls back to random posts
        self._pick_displayed()

    def _pick_displayed(self) -> None:
        """Samples shown under the prompt; same picks the wait screen will make."""
        if self._original is None:
            self._similar = pick_similar(self._pool, self._ranked_prompt)
        else:
            self._similar = pick_similar(self._pool, self._ranked_prompt, R_N_SIMILAR)
            taken = {p.path for p in self._similar}
            self._like_original = pick_similar(
                [p for p in self._pool if p.path not in taken],
                self._ranked_original,
                R_N_LIKE_ORIGINAL,
            )
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

    #echo-wait-modal LoadingIndicator {
        height: 3;
    }

    #echo-wait-status {
        margin-top: 1;
        text-align: center;
    }

    #echo-wait-hint {
        text-align: center;
        color: $text-muted;
    }
    """

    def __init__(self, request: str, posts: list[Post], directory: Path, engine, index=None, random_pick: Post | None = None, writer: str = WRITER_ECHO, original: Post | None = None) -> None:
        super().__init__()
        self._original = original
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
            yield Label("Esc to cancel", id="echo-wait-hint")

    def _wait_text(self) -> str:
        who = "the system LLM" if self._writer == WRITER_SYSTEM else "Echo"
        return WAIT_TEXT.format(who=who)

    def on_mount(self) -> None:
        self._run()

    @work(exclusive=True)
    async def _run(self) -> None:
        try:
            ranked = ranked_original = None
            if self._index is not None:
                self.query_one("#echo-wait-status", Label).update("Finding similar posts…")
                try:
                    ranked = await asyncio.to_thread(self._index.rank_by_text, self._request)
                    if self._original is not None:
                        ranked_original = await asyncio.to_thread(
                            self._index.rank_by_text, original_query(self._original)
                        )
                except EMBEDDING_ERRORS:
                    log_exception("echo: rank for samples")
                    ranked = ranked_original = None  # embeddings unavailable: random sample instead
            self.query_one("#echo-wait-status", Label).update(self._wait_text())
            if self._original is None:
                samples = select_samples(self._posts, ranked, random_pick=self._random_pick)
            else:
                samples = select_refactor_samples(
                    self._posts, self._original, ranked, ranked_original
                )
            if not samples:
                raise EchoError("No published posts to use as writing samples")
            writing = (await asyncio.to_thread(load_project, self._directory)).writing
            language = detect_language(" ".join(p.content for p in samples))
            message = build_message(
                samples, self._request, self._original,
                constraints=writing.for_language(language),
            )
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
            # A refactor replaces the original, so it inherits its category,
            # thumbnail and translationKey
            inherited = inherited_fields(self._original) if self._original else {}
            category = inherited.get("category")
            if category is None:
                self.query_one("#echo-wait-status", Label).update("Picking a category…")
                categories = await asyncio.to_thread(load_categories, self._directory)
                if not categories:
                    self.notify(
                        "No categories found (check .pages.yml); category left as TBD",
                        severity="warning",
                    )
                category = await pick_category(self._engine, title, body, categories)
            path = create_draft(
                self._directory, text, self._request, category,
                refactor_of=self._original.filename if self._original else None,
                thumbnail=inherited.get("thumbnail"),
                translation_key=inherited.get("translation_key"),
            )
            self.notify(f"Written by {who}")
        except EchoError as e:
            self.dismiss(self._empty_draft(str(e)))
            return
        except Exception as e:  # UI boundary: report it, keep the traceback in the log
            log_exception("echo draft")
            self.dismiss(self._empty_draft(f"Echo draft failed: {e}"))
            return
        self.dismiss(path)

    def _empty_draft(self, error: str) -> Path | None:
        """After a failure, keep the prompt: an empty draft with it in the frontmatter."""
        try:
            inherited = inherited_fields(self._original) if self._original else {}
            path = create_draft(
                self._directory, "", self._request, inherited.get("category"),
                refactor_of=self._original.filename if self._original else None,
                thumbnail=inherited.get("thumbnail"),
                translation_key=inherited.get("translation_key"),
            )
        except (OSError, ValueError):
            log_exception("echo: empty draft after failure")
            self.notify(error, severity="error", timeout=10)
            return None
        self.notify(
            f"{error}\nAn empty draft was created to keep your prompt "
            f"(frontmatter field 'prompt'): {path.name}",
            severity="error", timeout=15,
        )
        return path

    def action_cancel(self) -> None:
        self.workers.cancel_all()
        self.notify("Cancelled")
        self.dismiss(None)
