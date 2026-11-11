"""Draft flow (`h`, `R`): type a prompt, wait for the system LLM, get a new draft post.

With "Research the web first" checked, a search and a brief come before the
writing: ResearchScreen collects them, BriefReviewScreen lets the author edit
the brief, and DraftWaitScreen hands the approved brief to the writer.
"""

import asyncio
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button, Checkbox, Input, Label, LoadingIndicator, Select, Static, TextArea,
)
from textual.containers import VerticalScroll

from hugin import factcheck as fc
from hugin import research as web
from hugin import verify

from hugin.drafting import (
    LLM_ERRORS,
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
    recreatable_links,
    refactor_losses,
    unlink_internal,
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

    #draft-research-row {
        height: auto;
        margin-bottom: 1;
    }

    #draft-research {
        width: auto;
    }

    #draft-query {
        width: 1fr;
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
        search_key: str | None = None,
    ) -> None:
        super().__init__()
        self._search_key = search_key
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
        # Search query when the web research is on ("" = derive it), else None
        self.research_query: str | None = None

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
            with Horizontal(id="draft-research-row"):
                # A refactor checks the original's claims; a new post gets a brief
                what = (
                    "Research the web first" if self._original is None
                    else "Fact-check the original on the web first"
                )
                yield Checkbox(
                    what if self._search_key
                    else f"{what} (no {web.SEARCH_SECRET} in the vault)",
                    value=False, disabled=not self._search_key, id="draft-research",
                )
                if self._original is None:
                    yield Input(
                        placeholder="search query (empty: derived from the prompt)",
                        id="draft-query", disabled=True,
                    )
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
        room = int(self.size.height * 0.9) - 24
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

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "draft-research" and self.query("#draft-query"):
            self.query_one("#draft-query", Input).disabled = not event.value

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
        query = self.query("#draft-query")
        self.research_query = (
            (query.first(Input).value.strip() if query else "")
            if self.query_one("#draft-research", Checkbox).value else None
        )
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

    def __init__(self, request: str, posts: list[Post], directory: Path, engine, index=None, random_pick: Post | None = None, original: Post | None = None, words: int | None = None, author: str | None = None, profile: str | None = None, research: "web.Research | None" = None, brief: str | None = None, fact_check: "fc.FactCheck | None" = None) -> None:
        super().__init__()
        self._fact_check = fact_check
        # Approved web research; used only when both are given
        self._research = research if brief and brief.strip() else None
        self._brief = brief
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
                research=web.writer_context(self._brief, self._research)
                if self._research else None,
                claims=fc.writer_context(self._fact_check) if self._fact_check else None,
            )
            text = await write_draft(message, self._engine)
            who = f"{self._engine.id} / {self._engine.model}"
            title, body = parse_answer(text, self._request)
            if self._original is not None:
                # Only source links stay; the author recreates the others
                body = unlink_internal(body)
                text = f"{title}\n\n{body}"
            issues = await self._check(body)
            if issues and not await self.app.push_screen_wait(DraftCheckScreen(issues)):
                self.notify("Draft discarded")
                self.dismiss(None)
                return
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
                cover=inherited.get("cover"),
                author=self._author,
                author_field=project.authors.author_field,
            )
            try:
                log = None
                if self._research is not None:
                    log = await asyncio.to_thread(
                        web.save_log, self._research, self._brief, self._request, path
                    )
                elif self._fact_check is not None:
                    log = await asyncio.to_thread(
                        fc.save_log, self._fact_check, self._request, path
                    )
                if log is not None:
                    self.notify(f"Research kept in {log}")
            except OSError:
                log_exception("draft: save research log")
            self.notify(f"Written by {who}")
            if self._original is not None:
                own, affiliate = recreatable_links(self._original.content)
                if own or affiliate:
                    self.notify(
                        f"Left out of the refactor: {own} link(s) to your posts and "
                        f"{affiliate} affiliate link(s). Add them back with o, d and z.",
                        timeout=15,
                    )
        except DraftError as e:
            self.dismiss(self._empty_draft(str(e)))
            return
        except Exception as e:  # UI boundary: report it, keep the traceback in the log
            log_exception("draft")
            self.dismiss(self._empty_draft(f"Draft failed: {e}"))
            return
        self.dismiss(path)

    async def _check(self, body: str) -> list[tuple[str, str]]:
        """What to show before saving: losses, unknown links, unsupported statements."""
        original = self._original.content if self._original is not None else None
        issues = refactor_losses(original, body) if original is not None else []
        issues += verify.unknown_links(
            body, verify.allowed_links(original, self._research, self._fact_check)
        )
        # Statements are only checked against references; a plain new post has none
        if original is None and self._research is None:
            return issues
        self.query_one("#draft-wait-status", Label).update(
            f"{escape(self._engine.id)} is checking the draft…"
        )
        found = self._research or (self._fact_check.research if self._fact_check else None)
        issues += await verify.check_statements(
            self._engine, body, original,
            brief=web.writer_context(self._brief, self._research) if self._research else None,
            decisions=fc.writer_context(self._fact_check) if self._fact_check else None,
            sources=verify.linked_sources(body, found),
        )
        return issues

    def _empty_draft(self, error: str) -> Path | None:
        """After a failure, keep the prompt: an empty draft with it in the frontmatter."""
        try:
            inherited = inherited_fields(self._original) if self._original else {}
            path = create_draft(
                self._directory, "", self._request, inherited.get("category"),
                refactor_of=self._original.filename if self._original else None,
                thumbnail=inherited.get("thumbnail"),
                translation_key=inherited.get("translation_key"),
                cover=inherited.get("cover"),
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


class ResearchScreen(ModalScreen["tuple[web.Research | None, str] | None"]):
    """Spinner while Hugin searches, reads pages and writes the brief.

    Dismisses with (research, brief), with (None, error) when the research
    failed (the review screen shows it), or None when cancelled.
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = DraftWaitScreen.DEFAULT_CSS.replace("DraftWaitScreen", "ResearchScreen")

    def __init__(self, request: str, query: str, engine, api_key: str | None,
                 language: str, original: Post | None = None) -> None:
        super().__init__()
        self._request = request
        self._query = query
        self._engine = engine
        self._api_key = api_key
        self._language = language
        self._original = original

    def compose(self) -> ComposeResult:
        with Vertical(id="draft-wait-modal"):
            yield LoadingIndicator()
            yield Label("Preparing the search…", id="draft-wait-status")
            yield Label("Esc to cancel", id="draft-wait-hint")

    def on_mount(self) -> None:
        self._run()

    def _status(self, text: str) -> None:
        self.query_one("#draft-wait-status", Label).update(text)

    @work(exclusive=True)
    async def _run(self) -> None:
        title = (
            str(self._original.metadata.get("title") or self._original.path.stem)
            if self._original is not None else None
        )
        try:
            if self._engine is None or not self._engine.available:
                raise web.ResearchError("No usable system LLM to write the brief")
            query = self._query
            if not query:
                self._status("Writing a search query…")
                try:
                    query = await web.derive_query(self._engine, self._request, self._language, title)
                except LLM_ERRORS:
                    log_exception("research: derive query")
                    query = web.fallback_query(title or self._request)
            self._status(f"Searching: {escape(query)}")
            found = await web.gather(query, self._api_key, self._language)
            self._status(
                f"Read {found.pages_read} of {len(found.results)} pages. "
                f"{escape(self._engine.id)} is writing the brief…"
            )
            try:
                brief = await web.write_brief(found, self._request, self._engine, title)
            except LLM_ERRORS as e:
                raise web.ResearchError(f"{self._engine.id} failed on the brief: {e}") from e
        except web.ResearchError as e:
            self.dismiss((None, str(e)))
            return
        except Exception as e:  # UI boundary: report it, keep the traceback in the log
            log_exception("research")
            self.dismiss((None, f"Research failed: {e}"))
            return
        self.dismiss((found, brief))

    def action_cancel(self) -> None:
        self.workers.cancel_all()
        self.notify("Cancelled")
        self.dismiss(None)


# BriefReviewScreen result meaning "write the draft without the research"
WITHOUT_RESEARCH = ""


class BriefReviewScreen(ModalScreen[str | None]):
    """The brief, editable, with the sources and their read status.

    Dismisses with the edited brief, WITHOUT_RESEARCH to write without it,
    or None to cancel the draft. After a failed research only the error is
    shown, with the choice to write without it.
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+s", "submit", "Write draft"),
    ]

    DEFAULT_CSS = """
    BriefReviewScreen {
        align: center middle;
    }

    #brief-modal {
        width: 95%;
        max-width: 130;
        height: 90%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #brief-title {
        text-style: bold;
    }

    #brief-hint, #brief-sources {
        color: $text-muted;
        margin-bottom: 1;
    }

    #brief-error {
        color: $error;
        margin-bottom: 1;
    }

    #brief-text {
        height: 1fr;
        margin-bottom: 1;
    }

    #brief-sources {
        height: auto;
        max-height: 9;
    }

    #brief-buttons {
        height: auto;
    }

    #brief-buttons Button {
        margin-right: 1;
    }
    """

    def __init__(self, research: "web.Research | None", brief: str) -> None:
        super().__init__()
        self._research = research
        self._brief = brief

    def compose(self) -> ComposeResult:
        with Vertical(id="brief-modal"):
            if self._research is None:
                yield Label("Web research failed", id="brief-title")
                yield Static(escape(self._brief), id="brief-error")
                yield Static("You can write the draft without the research, or cancel.",
                             id="brief-hint")
                with Horizontal(id="brief-buttons"):
                    yield Button("Write without research", id="btn-brief-skip", variant="primary")
                    yield Button("Cancel", id="btn-brief-cancel")
                return
            r = self._research
            yield Label(f"Research brief — {escape(r.query)}", id="brief-title")
            yield Static(
                f"Brave Search, {r.country}/{r.search_lang}, "
                f"{r.fetched_at:%Y-%m-%d %H:%M}. Read {r.pages_read} of "
                f"{len(r.results)} pages. Edit the brief: the writer gets it as "
                "you leave it, plus the source list. Ctrl+S writes the draft, Esc cancels.",
                id="brief-hint",
            )
            unknown = web.unknown_citations(self._brief, r)
            if unknown:
                yield Static(
                    "The brief cites sources that do not exist: "
                    + ", ".join(f"[{n}]" for n in unknown) + ". Check those facts.",
                    id="brief-error",
                )
            yield TextArea(self._brief, language="markdown", id="brief-text")
            yield Static(self._sources_text(), id="brief-sources")
            with Horizontal(id="brief-buttons"):
                yield Button("Write draft (Ctrl+S)", id="btn-brief-send", variant="primary")
                yield Button("Write without research", id="btn-brief-skip")
                yield Button("Cancel", id="btn-brief-cancel")

    def _sources_text(self) -> str:
        r = self._research
        lines = []
        for result in r.results:
            status = r.status(result)
            mark = "[green]read[/green]" if status == "read" else f"[yellow]{escape(status)}[/yellow]"
            lines.append(f"  [b][{result.rank}][/b] {escape(result.title)} — {escape(result.url)}  {mark}")
        return "Sources:\n" + "\n".join(lines)

    def on_mount(self) -> None:
        if self._research is not None:
            self.query_one("#brief-text", TextArea).focus()
        else:
            self.query_one("#btn-brief-skip", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-brief-send":
            self.action_submit()
        elif event.button.id == "btn-brief-skip":
            self.dismiss(WITHOUT_RESEARCH)
        else:
            self.action_cancel()

    def action_submit(self) -> None:
        if self._research is None:
            return
        brief = self.query_one("#brief-text", TextArea).text.strip()
        if not brief:
            self.notify("The brief is empty: write one, or use \"Write without research\".",
                        severity="warning")
            return
        self.dismiss(brief)

    def action_cancel(self) -> None:
        self.dismiss(None)


class DraftCheckScreen(ModalScreen[bool]):
    """What the checks found in a draft; dismisses True to save it."""

    BINDINGS = [
        ("escape", "discard", "Discard"),
        ("ctrl+s", "save", "Save draft"),
    ]

    DEFAULT_CSS = """
    DraftCheckScreen {
        align: center middle;
    }

    #check-modal {
        width: 90%;
        max-width: 120;
        height: auto;
        max-height: 85%;
        border: round $warning;
        background: $surface;
        padding: 1 2;
    }

    #check-title {
        text-style: bold;
    }

    #check-hint {
        color: $text-muted;
        margin-bottom: 1;
    }

    #check-list {
        height: auto;
        max-height: 1fr;
        margin-bottom: 1;
    }

    #check-buttons {
        dock: bottom;
    }

    #check-buttons {
        height: auto;
    }

    #check-buttons Button {
        margin-right: 1;
    }
    """

    def __init__(self, losses: list[tuple[str, str]]) -> None:
        super().__init__()
        self._losses = losses

    def compose(self) -> ComposeResult:
        with Vertical(id="check-modal"):
            yield Label("Check the draft before saving", id="check-title")
            yield Static(
                "Losses and links come from a text comparison; claims and "
                "attributions from a second LLM pass, which can miss problems "
                "and flag some that are not. Some removals can be right (a "
                "wrong link, an outdated price). Save the draft and fix the "
                "rest in the editor, or discard it. Ctrl+S saves, Esc discards.",
                id="check-hint",
            )
            with VerticalScroll(id="check-list"):
                yield Static("\n".join(
                    f"  [b]{escape(kind)}[/b]  {escape(detail)}" for kind, detail in self._losses
                ))
            with Horizontal(id="check-buttons"):
                yield Button("Save draft (Ctrl+S)", id="btn-check-save", variant="primary")
                yield Button("Discard", id="btn-check-discard")

    def on_mount(self) -> None:
        self.query_one("#btn-check-save", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-check-save")

    def action_save(self) -> None:
        self.dismiss(True)

    def action_discard(self) -> None:
        self.dismiss(False)


class FactCheckScreen(ModalScreen["tuple[fc.FactCheck | None, str] | None"]):
    """Spinner while Hugin lists the original's claims, searches and weighs them.

    Dismisses with (check, ""), with (None, error) when the check failed
    (the review screen shows it), or None when cancelled.
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = DraftWaitScreen.DEFAULT_CSS.replace("DraftWaitScreen", "FactCheckScreen")

    def __init__(self, original: Post, engine, api_key: str | None, language: str) -> None:
        super().__init__()
        self._original = original
        self._engine = engine
        self._api_key = api_key
        self._language = language

    def compose(self) -> ComposeResult:
        with Vertical(id="draft-wait-modal"):
            yield LoadingIndicator()
            yield Label("Listing the claims of the original…", id="draft-wait-status")
            yield Label("Esc to cancel", id="draft-wait-hint")

    def on_mount(self) -> None:
        self._run()

    def _status(self, text: str) -> None:
        self.query_one("#draft-wait-status", Label).update(text)

    @work(exclusive=True)
    async def _run(self) -> None:
        title = str(self._original.metadata.get("title") or self._original.path.stem)
        try:
            if self._engine is None or not self._engine.available:
                raise web.ResearchError("No usable system LLM for the fact check")
            if not self._api_key:
                raise web.ResearchError(f"No Brave Search key in the vault ({web.SEARCH_SECRET})")
            try:
                claims = await fc.list_claims(self._engine, title, self._original.content)
            except LLM_ERRORS as e:
                raise web.ResearchError(f"{self._engine.id} failed listing the claims: {e}") from e
            self._status(f"Searching {len(claims)} claims…")
            check = await fc.gather(
                claims, self._api_key, self._language,
                progress=lambda done, total: self._status(
                    f"Searched {done} of {total} claims…" if done < total
                    else "Reading the pages found…"
                ),
            )
            self._status(f"Read {check.research.pages_read} pages. "
                         f"{escape(self._engine.id)} is weighing the claims…")
            try:
                await fc.weigh(check, self._engine, title)
            except LLM_ERRORS as e:
                raise web.ResearchError(f"{self._engine.id} failed weighing the claims: {e}") from e
        except web.ResearchError as e:
            self.dismiss((None, str(e)))
            return
        except Exception as e:  # UI boundary: report it, keep the traceback in the log
            log_exception("fact check")
            self.dismiss((None, f"Fact check failed: {e}"))
            return
        self.dismiss((check, ""))

    def action_cancel(self) -> None:
        self.workers.cancel_all()
        self.notify("Cancelled")
        self.dismiss(None)


VERDICT_LABELS = {
    "keep": "keep",
    "correct": "correct",
    "remove": "remove",
    "unconfirmed": "not confirmed",
}


class ClaimsReviewScreen(ModalScreen["fc.FactCheck | str | None"]):
    """One row per claim: verdict and correction editable, sources shown.

    Dismisses with the check as edited, WITHOUT_RESEARCH to write without
    it, or None to cancel. After a failed check only the error is shown.
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+s", "submit", "Write draft"),
    ]

    DEFAULT_CSS = BriefReviewScreen.DEFAULT_CSS.replace("BriefReviewScreen", "ClaimsReviewScreen") + """
    #claims {
        height: 1fr;
        margin-bottom: 1;
    }

    .claim {
        height: auto;
        margin-bottom: 1;
    }

    .claim-text {
        text-style: bold;
    }

    .claim-row {
        height: auto;
    }

    .claim-row Select {
        width: 22;
    }

    .claim-row Input {
        width: 1fr;
    }

    .claim-meta {
        color: $text-muted;
    }
    """

    def __init__(self, check: "fc.FactCheck | None", error: str = "") -> None:
        super().__init__()
        self._check = check
        self._error = error

    def compose(self) -> ComposeResult:
        with Vertical(id="brief-modal"):
            if self._check is None:
                yield Label("Fact check failed", id="brief-title")
                yield Static(escape(self._error), id="brief-error")
                yield Static("You can write the draft without it, or cancel.", id="brief-hint")
                with Horizontal(id="brief-buttons"):
                    yield Button("Write without fact check", id="btn-brief-skip", variant="primary")
                    yield Button("Cancel", id="btn-brief-cancel")
                return
            r = self._check.research
            counts = {v: sum(c.verdict == v for c in self._check.claims) for v in fc.VERDICTS}
            yield Label(f"Fact check — {len(self._check.claims)} claims", id="brief-title")
            yield Static(
                f"{counts['keep']} keep, {counts['correct']} correct, {counts['remove']} remove, "
                f"{counts['unconfirmed']} not confirmed. Brave Search, {r.pages_read} of "
                f"{len(r.results)} pages read. Change any decision or correction: the writer "
                "applies them as you leave them; claims not confirmed stay as the original "
                "says. Ctrl+S writes the draft, Esc cancels.",
                id="brief-hint",
            )
            with VerticalScroll(id="claims"):
                for c in self._check.claims:
                    with Vertical(classes="claim"):
                        yield Static(f"[{c.n}] {escape(c.text)}", classes="claim-text")
                        with Horizontal(classes="claim-row"):
                            yield Select(
                                [(label, v) for v, label in VERDICT_LABELS.items()],
                                value=c.verdict, allow_blank=False, id=f"verdict-{c.n}",
                            )
                            yield Input(
                                value=c.correction, id=f"correction-{c.n}",
                                placeholder="corrected claim (for correct)",
                            )
                        yield Static(self._meta(c), classes="claim-meta")
            yield Static(self._sources_text(), id="brief-sources")
            with Horizontal(id="brief-buttons"):
                yield Button("Write draft (Ctrl+S)", id="btn-brief-send", variant="primary")
                yield Button("Write without fact check", id="btn-brief-skip")
                yield Button("Cancel", id="btn-brief-cancel")

    def _meta(self, c: "fc.Claim") -> str:
        parts = []
        if c.note:
            parts.append(escape(c.note))
        if c.cited:
            parts.append("cites " + " ".join(f"\\[{n}]" for n in c.cited))
        elif c.sources:
            parts.append("found " + " ".join(f"\\[{n}]" for n in c.sources))
        if c.error:
            parts.append(f"[yellow]{escape(c.error)}[/yellow]")
        parts.append(f"query: {escape(c.query)}" + (" (English)" if c.english else ""))
        return "  ·  ".join(parts)

    def _sources_text(self) -> str:
        r = self._check.research
        cited = sorted({n for c in self._check.claims for n in c.cited})
        lines = []
        for n in cited:
            result = r.results[n - 1]
            status = r.status(result)
            mark = "[green]read[/green]" if status == "read" else f"[yellow]{escape(status)}[/yellow]"
            lines.append(f"  [b]\\[{n}][/b] {escape(result.title)} — {escape(result.url)}  {mark}")
        return "Sources cited:\n" + ("\n".join(lines) if lines else "  [i]none[/i]")

    def on_mount(self) -> None:
        if self._check is None:
            self.query_one("#btn-brief-skip", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-brief-send":
            self.action_submit()
        elif event.button.id == "btn-brief-skip":
            self.dismiss(WITHOUT_RESEARCH)
        else:
            self.action_cancel()

    def action_submit(self) -> None:
        if self._check is None:
            return
        missing = []
        for c in self._check.claims:
            c.verdict = self.query_one(f"#verdict-{c.n}", Select).value
            c.correction = " ".join(self.query_one(f"#correction-{c.n}", Input).value.split())
            if c.verdict == "correct" and not c.correction:
                missing.append(c.n)
        if missing:
            self.notify(
                "Write the correction for " + ", ".join(f"[{n}]" for n in missing)
                + ", or choose another decision.", severity="warning",
            )
            return
        self.dismiss(self._check)

    def action_cancel(self) -> None:
        self.dismiss(None)
