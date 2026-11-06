"""Draft flow (`h`, `R`): type a prompt, wait for the system LLM, get a new draft post."""

import asyncio
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button, Checkbox, Input, Label, LoadingIndicator, Select, Static, TextArea,
)

from hugin.drafting import (
    DEFAULT_TARGET_WORDS,
    MAX_TARGET_WORDS,
    MIN_TARGET_WORDS,
    MIN_SAMPLES,
    N_RANDOM,
    N_RECENT,
    N_SIMILAR,
    EMBEDDING_ERRORS,
    R_N_LIKE_ORIGINAL,
    R_N_RECENT,
    R_N_SIMILAR,
    DraftError,
    build_message,
    create_draft,
    has_enough_samples,
    parse_answer,
    _select_parts,
    draw_random,
    inherited_fields,
    original_query,
    pick_category,
    pick_similar,
    select_refactor_samples,
    select_samples,
    write_draft,
)
from hugin.authors import pick_author, post_author
from hugin.log import log_exception
from hugin.hugo import load_categories
from hugin.normalizer import detect_language
from hugin.project import load_project
from hugin.scanner import Post
from rich.markup import escape


WAIT_TEXT = "Waiting for {who} (can take several minutes)…"


SIMILAR_PENDING = f"{N_SIMILAR} closest to your prompt (picked when you send)"
R_SIMILAR_PENDING = f"{R_N_SIMILAR} closest to your prompt (picked when you send)"


class DraftPromptScreen(ModalScreen[str | None]):
    """Text area where the user describes the post.

    Dismisses with the prompt, or None when cancelled. With `original` it is the refactor
    variant: the samples are 2 latest, 2 closest to the prompt and 2 closest
    to that post.
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+s", "submit", "Send"),
    ]

    DEFAULT_CSS = """
    DraftPromptScreen {
        align: center middle;
    }

    #draft-modal {
        width: 90%;
        max-width: 110;
        height: auto;
        max-height: 90%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #draft-title {
        text-style: bold;
    }

    #draft-hint {
        color: $text-muted;
        margin-bottom: 1;
    }

    #draft-prompt {
        height: 10;
        margin-bottom: 1;
    }

    #draft-author-row {
        height: auto;
    }

    #draft-author {
        width: 40;
    }

    #draft-profile {
        margin-bottom: 1;
    }

    #draft-length {
        height: auto;
        margin-bottom: 1;
    }

    #draft-length Input {
        width: 14;
    }

    #draft-samples {
        height: auto;
        color: $text-muted;
        margin-bottom: 1;
    }

    #draft-buttons {
        height: auto;
    }

    #draft-buttons Button {
        margin-right: 1;
    }
    """

    DEBOUNCE_SECONDS = 1.0

    def __init__(
        self, posts: list[Post] | None = None, index=None, engine=None,
        original: Post | None = None, authors: list[str] | None = None,
        profiles: dict[str, str] | None = None, default_author: str | None = None,
    ) -> None:
        super().__init__()
        self._index = index
        self._engine = engine
        self._original = original
        self._posts = posts or []
        self._authors = authors or []
        self._profiles = profiles or {}
        # Refactors keep the original's author unless the operator changes it
        preferred = post_author(original) if original is not None else default_author
        self.author = pick_author(preferred, self._authors)
        self._recompute_pool()
        self._similar_note = R_SIMILAR_PENDING if original else SIMILAR_PENDING
        self._similar: list[Post] = []
        self._like_original: list[Post] = []
        self._ranked_prompt: list[str] | None = None
        self._ranked_original: list[str] | None = None
        self._debounce = None
        # Set when the prompt is sent; None for refactors (length comes from the original)
        self.target_words: int | None = None
        # Author profile to send with the request, set when the prompt is sent
        self.profile: str | None = None

    def _recompute_pool(self) -> None:
        """Recent posts and the candidates for the similar ones, for the chosen author."""
        self._recent, rest = _select_parts(self._posts, self._original, self.author)
        # Drawn once per author, so the title shown is the one that gets sent.
        self.random_pick = None if self._original else draw_random(rest)
        self._pool = [p for p in rest if p is not self.random_pick]

    def _has_profile(self) -> bool:
        return bool(self._profiles.get(self.author or ""))

    def compose(self) -> ComposeResult:
        with Vertical(id="draft-modal"):
            if self._original is None:
                heading = "New draft — describe the post"
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
            yield Label(heading, id="draft-title")
            refactor_note = (
                "The post is rewritten in full to replace the original (not a follow-up): "
                "say what to change or improve. "
                if self._original is not None else ""
            )
            yield Static(
                f"{refactor_note}{count} published posts go along as writing samples ({mix}). "
                f"Written by {escape(self._writer_label())}. Ctrl+S sends, Esc cancels.",
                id="draft-hint",
            )
            if self._authors:
                with Horizontal(id="draft-author-row"):
                    yield Label("Author: ")
                    yield Select(
                        [(a, a) for a in self._authors], value=self.author,
                        allow_blank=False, id="draft-author",
                    )
                yield Checkbox(
                    "Send the author profile", value=self._has_profile(),
                    disabled=not self._has_profile(), id="draft-profile",
                )
            yield TextArea(id="draft-prompt")
            if self._original is None:
                with Horizontal(id="draft-length"):
                    yield Label("Length (words): ")
                    yield Input(value=str(DEFAULT_TARGET_WORDS), id="draft-words", type="integer")
            yield Static(self._samples_text(), id="draft-samples")
            with Horizontal(id="draft-buttons"):
                yield Button("Send (Ctrl+S)", id="btn-draft-send", variant="primary")
                yield Button("Cancel", id="btn-draft-cancel")

    def on_mount(self) -> None:
        self.query_one("#draft-prompt", TextArea).focus()
        self._fit_prompt()
        if self._original is not None:
            self._rank_original()

    def on_resize(self) -> None:
        self._fit_prompt()

    def _fit_prompt(self) -> None:
        # Everything but the text area takes ~20 rows (border, title, hint,
        # samples, buttons); give the text area what is left, between 4 and 10.
        room = int(self.size.height * 0.9) - 21
        self.query_one("#draft-prompt", TextArea).styles.height = max(4, min(10, room))

    def _writer_label(self) -> str:
        if self._engine is None:
            return "the system LLM"
        return f"{self._engine.id} / {self._engine.model}"

    @staticmethod
    def _title(post: Post) -> str:
        return str(post.metadata.get("title") or post.path.stem)

    def _samples_text(self) -> str:

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
        if not lines:
            who = f" by {escape(self.author)}" if self.author else ""
            lines.append(f"  [i]no published posts{who}[/i]")
        return "Writing samples sent along:\n" + "\n".join(lines)

    def _refresh_samples(self) -> None:
        self.query_one("#draft-samples", Static).update(self._samples_text())

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "draft-author" or event.value == self.author:
            return
        self.author = event.value
        self._recompute_pool()
        self._similar = []
        self._like_original = []
        profile = self.query_one("#draft-profile", Checkbox)
        profile.value = self._has_profile()
        profile.disabled = not self._has_profile()
        self._pick_displayed()

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
            log_exception("draft: rank prompt")
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
            log_exception("draft: rank original")
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
        if event.button.id == "btn-draft-send":
            self.action_submit()
        else:
            self.action_cancel()

    def action_submit(self) -> None:
        text = self.query_one("#draft-prompt", TextArea).text.strip()
        if not text:
            self.notify("Write a prompt first.", severity="warning")
            return
        self.profile = self._profile_to_send()
        if not has_enough_samples(self._posts, self._original, self.author) and not self.profile:
            who = f" by {self.author}" if self.author else ""
            self.notify(
                f"Not enough published posts{who} to use as samples. "
                "Send the author profile, or add posts.",
                severity="warning", timeout=10,
            )
            return
        self.target_words = self._read_target_words() if self._original is None else None
        self.dismiss(text)

    def _profile_to_send(self) -> str | None:
        if not self._authors or not self.query_one("#draft-profile", Checkbox).value:
            return None
        return self._profiles.get(self.author or "") or None

    def _read_target_words(self) -> int:
        """The length typed in the form, clamped; the default when it is not a number."""
        raw = self.query_one("#draft-words", Input).value.strip()
        try:
            words = int(raw)
        except ValueError:
            return DEFAULT_TARGET_WORDS
        return max(MIN_TARGET_WORDS, min(MAX_TARGET_WORDS, words))

    def action_cancel(self) -> None:
        self.dismiss(None)


class DraftWaitScreen(ModalScreen[Path | None]):
    """Spinner while the system LLM writes; dismisses with the draft path (None on failure)."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    DraftWaitScreen {
        align: center middle;
    }

    #draft-wait-modal {
        width: 60;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #draft-wait-modal LoadingIndicator {
        height: 3;
    }

    #draft-wait-status {
        margin-top: 1;
        text-align: center;
    }

    #draft-wait-hint {
        text-align: center;
        color: $text-muted;
    }
    """

    def __init__(self, request: str, posts: list[Post], directory: Path, engine, index=None, random_pick: Post | None = None, original: Post | None = None, words: int | None = None, author: str | None = None, profile: str | None = None) -> None:
        super().__init__()
        self._original = original
        self._words = words
        self._author = author
        self._profile = profile
        self._engine = engine
        self._index = index
        self._random_pick = random_pick
        self._request = request
        self._posts = posts
        self._directory = directory

    def compose(self) -> ComposeResult:
        with Vertical(id="draft-wait-modal"):
            yield LoadingIndicator()
            yield Label(self._wait_text(), id="draft-wait-status")
            yield Label("Esc to cancel", id="draft-wait-hint")

    def _wait_text(self) -> str:
        who = f"{self._engine.id}" if self._engine is not None else "the system LLM"
        return WAIT_TEXT.format(who=who)

    def on_mount(self) -> None:
        self._run()

    @work(exclusive=True)
    async def _run(self) -> None:
        try:
            ranked = ranked_original = None
            if self._index is not None:
                self.query_one("#draft-wait-status", Label).update("Finding similar posts…")
                try:
                    ranked = await asyncio.to_thread(self._index.rank_by_text, self._request)
                    if self._original is not None:
                        ranked_original = await asyncio.to_thread(
                            self._index.rank_by_text, original_query(self._original)
                        )
                except EMBEDDING_ERRORS:
                    log_exception("draft: rank for samples")
                    ranked = ranked_original = None  # embeddings unavailable: random sample instead
            self.query_one("#draft-wait-status", Label).update(self._wait_text())
            if self._original is None:
                samples = select_samples(
                    self._posts, ranked, random_pick=self._random_pick, author=self._author
                )
            else:
                samples = select_refactor_samples(
                    self._posts, self._original, ranked, ranked_original, author=self._author
                )
            if not samples and not self._profile:
                raise DraftError("No published posts to use as writing samples")
            project = await asyncio.to_thread(load_project, self._directory)
            writing = project.writing
            language = detect_language(" ".join(p.content for p in samples) or self._request)
            message = build_message(
                samples, self._request, self._original,
                constraints=writing.for_language(language), words=self._words,
                profile=self._profile,
            )
            text = await write_draft(message, self._engine)
            who = f"{self._engine.id} / {self._engine.model}"
            title, body = parse_answer(text, self._request)
            # A refactor replaces the original, so it inherits its category,
            # thumbnail and translationKey
            inherited = inherited_fields(self._original) if self._original else {}
            category = inherited.get("category")
            if category is None:
                self.query_one("#draft-wait-status", Label).update("Picking a category…")
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
                author=self._author,
                author_field=project.authors.author_field,
            )
            self.notify(f"Written by {who}")
        except DraftError as e:
            self.dismiss(self._empty_draft(str(e)))
            return
        except Exception as e:  # UI boundary: report it, keep the traceback in the log
            log_exception("draft")
            self.dismiss(self._empty_draft(f"Draft failed: {e}"))
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
                author=self._author,
                author_field=load_project(self._directory).authors.author_field,
            )
        except (OSError, ValueError):
            log_exception("draft: empty draft after failure")
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
