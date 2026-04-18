"""Unified Hugin screen — tags, summaries, links, and editing."""

import json
import math
import re
from datetime import datetime
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Input,
    Label,
    Static,
    TextArea,
)

from hugin.config import HuginConfig
from hugin.embeddings import EmbeddingIndex
from hugin.engines import Engine, load_engines, save_last_engine
from hugin.hugo import HugoSite
from hugin.linker import (
    _find_whole_word,
    apply_links,
    check_anchor_viable,
    extract_existing_links,
    find_protected_zones,
    is_in_protected_zone,
    list_links,
    remove_specific_links,
    write_post_with_links,
)
from hugin.llm import (
    ANCHOR_SYSTEM_PROMPT,
    ANCHOR_USER_TEMPLATE,
    MAX_SUMMARY_CHARS,
    RETRY_PROMPT,
    SUGGEST_PROMPT,
    call_llm,
    parse_anchor_response,
    parse_suggestions,
    suggest_summary,
    suggest_tags,
)
from hugin.normalizer import normalize_tag, normalize_tags, strip_accents
from hugin.scanner import Post, format_pool_for_prompt
from hugin.state import mark_processed, save_state, get_last_post, set_last_post
from hugin.writer import write_summary, write_tags

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

STATE_BROWSING = "browsing"
STATE_LOADING = "loading"
STATE_REVIEWING = "reviewing"


class ClickableLink(Static, can_focus=True):
    """A focusable, clickable text that triggers navigation."""

    BINDINGS = [("enter", "activate", "Go")]

    DEFAULT_CSS = """
    ClickableLink {
        height: auto;
    }
    ClickableLink:focus {
        text-style: bold reverse;
    }
    """

    def __init__(self, text: str, post_index: int) -> None:
        super().__init__(f"[underline]{text}[/underline]")
        self.post_index = post_index

    def on_click(self) -> None:
        self._go()

    def action_activate(self) -> None:
        self._go()

    def _go(self) -> None:
        screen = self.screen
        if hasattr(screen, "_navigate_to_post"):
            screen._navigate_to_post(self.post_index)


class ConfirmClearScreen(ModalScreen[bool]):
    """Confirm cache clear and restart."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    ConfirmClearScreen {
        align: center middle;
    }

    #confirm-modal {
        width: 50;
        height: auto;
        border: solid $warning;
        background: $surface;
        padding: 1 2;
    }

    #confirm-modal Label {
        text-style: bold;
        margin-bottom: 1;
    }

    #confirm-buttons {
        height: auto;
        margin-top: 1;
    }

    #confirm-buttons Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-modal"):
            yield Label("Clear all caches and restart?")
            yield Static("Embedding cache will be rebuilt on restart.")
            with Horizontal(id="confirm-buttons"):
                yield Button("Clear & Restart", id="btn-confirm", variant="warning")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


class LoadingScreen(ModalScreen):
    """Non-interactive modal with animated spinner and status message."""

    DEFAULT_CSS = """
    LoadingScreen {
        align: center middle;
    }

    #loading-box {
        width: 46;
        height: 5;
        border: round $accent;
        background: $surface;
        padding: 1 2;
        content-align: center middle;
    }

    #loading-label {
        width: 100%;
        content-align: center middle;
        text-style: bold;
    }
    """

    def __init__(self, message: str = "Processing...") -> None:
        super().__init__()
        self._message = message
        self._frame = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="loading-box"):
            yield Static(
                f"{SPINNER_FRAMES[0]}  {self._message}",
                id="loading-label",
            )

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.08, self._tick)

    def _tick(self) -> None:
        self._frame += 1
        char = SPINNER_FRAMES[self._frame % len(SPINNER_FRAMES)]
        try:
            self.query_one("#loading-label", Static).update(
                f"{char}  {self._message}"
            )
        except Exception:
            pass


class HuginScreen(Screen):
    """Unified screen: tags, summaries, links, editor."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("t", "tags", "Tags"),
        ("s", "summary", "Summary"),
        ("i", "incoming", "Incoming"),
        ("o", "outgoing", "Outgoing"),
        ("l", "list_links", "List"),
        ("u", "suggest", "Suggest"),
        ("e", "editor", "Editor"),
        ("n", "pick_engine", "Engine"),
        ("m", "manage_tags", "Manage"),
        ("c", "clear_caches", "Clear"),
        ("escape", "back", "Back"),
    ]

    DEFAULT_CSS = """
    #banner {
        height: auto;
        content-align: left middle;
        text-style: bold;
        color: $accent;
        padding: 0 1;
        margin-bottom: 1;
    }

    #review-container {
        height: 1fr;
    }

    #post-list-panel {
        width: 3fr;
        border-right: solid $accent;
    }

    #post-table {
        height: 1fr;
    }

    #detail-panel {
        width: 2fr;
        padding: 1;
        overflow-y: auto;
    }

    #detail-panel > * {
        height: auto;
    }

    #suggested-tags-container {
        height: auto;
    }

    #suggested-tags-container Checkbox {
        height: auto;
    }

    .outgoing-context {
        margin-left: 4;
        height: auto;
    }

    .outgoing-url {
        color: $text-muted;
        margin-left: 4;
        margin-bottom: 1;
    }

    .incoming-link {
        height: auto;
    }

    .section-label {
        margin-top: 1;
        text-style: bold;
    }

    #engine-label {
        color: $text-muted;
        margin-bottom: 1;
    }

    #manual-tags-input {
        margin-top: 1;
    }

    #review-buttons {
        height: auto;
        margin-top: 1;
    }

    .hidden {
        display: none;
    }

    """

    BANNER = """\
  _  _           _
 | || |_  _ __ _(_)_ _
 | __ | || / _` | | ' \\
 |_||_|\\_,_\\__, |_|_||_|
           |___/"""

    def __init__(
        self,
        posts: list[Post],
        all_posts: list[Post],
        engine: Engine,
        pool: dict[str, int],
        state: dict,
        directory: Path,
        config: HuginConfig,
        site: HugoSite,
        index: EmbeddingIndex,
    ) -> None:
        super().__init__()
        self.posts = posts
        self.all_posts = all_posts
        self.engine = engine
        self.pool = pool
        self.state = state
        self.directory = directory
        self.config = config
        self.site = site
        self.index = index
        self.current_index = 0
        self.suggested_tags: list[str] = []
        self._existing_checkboxes: list[Checkbox] = []
        self._suggested_checkboxes: list[Checkbox] = []
        self._outgoing_checkboxes: list[Checkbox] = []
        self._listed_links: list[dict] = []
        self._row_keys: list[str] = []
        self._spinner_frame = 0
        self._spinning_row: int | None = None
        self._done_rows: set[int] = set()
        self._spinner_timer: Timer | None = None
        self._state = STATE_BROWSING
        self._mode = ""
        self._suggested_summary = ""
        self._suggested_topics: list[str] = []
        self._incoming_index: dict[str, int] = {}
        self._session_outgoing: dict[str, list[dict]] = {}
        self._loading_screen: LoadingScreen | None = None

    def compose(self) -> ComposeResult:
        yield Static(self.BANNER, id="banner")

        with Horizontal(id="review-container"):
            with Vertical(id="post-list-panel"):
                table = DataTable(id="post-table", cursor_type="row", zebra_stripes=True)
                yield table

            with Vertical(id="detail-panel"):
                yield Static("", id="engine-label")
                yield Label(id="progress-label")
                yield Static("", id="post-meta")
                yield Label("", classes="section-label", id="section-header")
                yield Vertical(id="suggested-tags-container")
                yield Input(
                    placeholder="Manual tags (comma-separated)",
                    id="manual-tags-input",
                    classes="hidden",
                )
                with Horizontal(id="review-buttons", classes="hidden"):
                    yield Button("Apply", id="btn-apply", variant="primary")
                    yield Button("Skip", id="btn-skip")

        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#post-table", DataTable)
        table.add_column("", key="status", width=1)
        table.add_column("Post", key="title")

        from rich.text import Text
        for i, post in enumerate(self.posts):
            title = post.metadata.get("title", post.filename)
            if post.metadata.get("draft"):
                cell = Text(f"[DRAFT] {title}", style="dim")
            else:
                cell = Text(title)
            status = "—" if self.index.has_no_outgoing(post) else " "
            table.add_row(status, cell, key=f"post-{i}")
            self._row_keys.append(f"post-{i}")

        self._spinner_timer = self.set_interval(0.08, self._tick_spinner)
        self._build_incoming_index()
        self._update_engine_label()

        # Restore last selected post
        last = get_last_post(self.state)
        if last:
            for i, post in enumerate(self.posts):
                if post.filename == last:
                    self.current_index = i
                    table.move_cursor(row=i)
                    break

        self._update_detail_panel()

    # --- Incoming index ---

    def _build_incoming_index(self) -> None:
        counts: dict[str, int] = {}
        for post in self.all_posts:
            for url in extract_existing_links(post.content):
                counts[url] = counts.get(url, 0) + 1
        self._incoming_index = counts

    # --- Spinner & loading overlay ---

    def _tick_spinner(self) -> None:
        if self._spinning_row is not None:
            self._spinner_frame += 1
            char = SPINNER_FRAMES[self._spinner_frame % len(SPINNER_FRAMES)]
            table = self.query_one("#post-table", DataTable)
            table.update_cell(self._row_keys[self._spinning_row], "status", char)

    def _start_spinner(self, index: int, message: str = "Processing...") -> None:
        self._spinning_row = index
        self._spinner_frame = 0
        self._loading_screen = LoadingScreen(message)
        self.app.push_screen(self._loading_screen)

    def _stop_spinner(self, done: bool = False) -> None:
        if self._spinning_row is not None:
            table = self.query_one("#post-table", DataTable)
            if done:
                table.update_cell(self._row_keys[self._spinning_row], "status", "✓")
                self._done_rows.add(self._spinning_row)
            else:
                table.update_cell(self._row_keys[self._spinning_row], "status", " ")
            self._spinning_row = None
        if self._loading_screen is not None:
            try:
                self._loading_screen.dismiss()
            except Exception:
                pass
            self._loading_screen = None

    def _mark_table_row(self, index: int, symbol: str) -> None:
        table = self.query_one("#post-table", DataTable)
        table.update_cell(self._row_keys[index], "status", symbol)

    # --- Engine ---

    def _update_engine_label(self) -> None:
        label = self.query_one("#engine-label", Static)
        label.update(f"Engine: {self.engine.id} ({self.engine.model})")

    def action_pick_engine(self) -> None:
        if self._state != STATE_BROWSING:
            return
        from hugin.tui.engine_picker import EnginePickerScreen
        engines = load_engines()

        def on_pick(engine: Engine | None) -> None:
            if engine is not None:
                self.engine = engine
                save_last_engine(engine)
                self._update_engine_label()
                self.notify(f"Engine: {engine.id} ({engine.model})")

        self.app.push_screen(EnginePickerScreen(engines, self.engine.id), on_pick)

    # --- Navigation ---

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self._state != STATE_BROWSING:
            return
        if event.cursor_row is not None and event.cursor_row != self.current_index:
            self.current_index = event.cursor_row
            self._update_detail_panel()

    def _navigate_to_post(self, index: int) -> None:
        if 0 <= index < len(self.posts):
            self.current_index = index
            self._state = STATE_BROWSING
            table = self.query_one("#post-table", DataTable)
            table.move_cursor(row=index)
            self._update_detail_panel()

    def _find_post_index(self, abs_path: str) -> int | None:
        for i, post in enumerate(self.posts):
            if str(post.path.resolve()) == abs_path:
                return i
        return None

    # --- Detail panel ---

    def _update_detail_panel(self) -> None:
        from rich.table import Table

        post = self.posts[self.current_index]
        total = len(self.posts)

        self.query_one("#progress-label", Label).update(
            f"{self.current_index + 1}/{total} — {post.filename}"
        )

        meta = post.metadata
        css_vars = self.app.get_css_variables()
        accent = self.app.current_theme.accent if hasattr(self.app, "current_theme") else "cyan"
        stripe_bg = css_vars.get("surface-darken-1", "#2d2d2d")

        table = Table(
            show_header=False, box=None, padding=(0, 1, 0, 0),
            row_styles=["", f"on {stripe_bg}"],
        )
        table.add_column("Field", style=f"bold {accent}", no_wrap=True)
        table.add_column("Value")

        skip_last = ("description", "tags")
        for key, value in meta.items():
            if key in skip_last:
                continue
            row = self._format_meta_value(value)
            if row is not None:
                table.add_row(key, row)

        desc = meta.get("description", "")
        if desc:
            table.add_row("description", f"{desc} ({len(str(desc))} chars)")
        tags = meta.get("tags")
        if tags:
            table.add_row("tags", ", ".join(str(t) for t in tags))
        elif tags is not None:
            table.add_row("tags", "(none)")

        # Link counts
        outgoing_count = len(extract_existing_links(post.content))
        word_count = len(post.content.split())
        budget = min(
            self.config.links.max_per_post,
            math.floor(word_count / self.config.links.words_per_link),
        )
        post_url = self.index.get_post_url(post)
        incoming_count = self._incoming_index.get(post_url, 0) if post_url else 0

        table.add_row("links out", f"{outgoing_count}/{budget}")
        table.add_row("links in", str(incoming_count))

        if budget == 0:
            table.add_row("outgoing", "[bold red]too short[/bold red]")
        elif self.index.has_no_outgoing(post):
            table.add_row("outgoing", "[dim]no opportunities[/dim]")

        self.query_one("#post-meta", Static).update(table)
        self._clear_action_area()

    @staticmethod
    def _format_meta_value(value) -> str | None:
        if value is None or value == "":
            return None
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return str(value)

    def _clear_action_area(self) -> None:
        self.suggested_tags = []
        self._suggested_summary = ""
        self._existing_checkboxes = []
        self._suggested_checkboxes = []
        self._outgoing_checkboxes = []
        self._listed_links = []
        self._suggested_topics = []
        self._mode = ""

        container = self.query_one("#suggested-tags-container")
        container.remove_children()
        self.query_one("#section-header", Label).update("")
        manual_input = self.query_one("#manual-tags-input", Input)
        manual_input.value = ""
        manual_input.add_class("hidden")
        self.query_one("#review-buttons").add_class("hidden")

    # --- Context extraction (for link display) ---

    @staticmethod
    def _extract_context(content: str, anchor: str, chars: int = 80) -> tuple[str, str]:
        pos = _find_whole_word(content, anchor)
        if pos == -1:
            pos = content.find(anchor)
        if pos == -1:
            return ("", "")

        start = max(0, pos - chars)
        end = min(len(content), pos + len(anchor) + chars)

        before = content[start:pos]
        after = content[pos + len(anchor):end]

        if start > 0:
            space = before.find(" ")
            if space != -1:
                before = before[space + 1:]
            before = "…" + before

        if end < len(content):
            space = after.rfind(" ")
            if space != -1:
                after = after[:space]
            after = after + "…"

        before = before.replace("\n", " ")
        after = after.replace("\n", " ")
        return (before, after)

    # === TAGS ===

    def action_tags(self) -> None:
        if self._state != STATE_BROWSING:
            return
        self._state = STATE_LOADING
        post = self.posts[self.current_index]
        self._clear_action_area()
        self._mode = "tags"
        self._start_spinner(self.current_index, "Generating tags...")
        self._call_llm_tags(post)

    @work(exclusive=True)
    async def _call_llm_tags(self, post: Post) -> None:
        pool_str = format_pool_for_prompt(self.pool)
        try:
            raw_tags = await suggest_tags(
                self.engine, post.metadata, post.content, pool_str,
            )
            normalized = normalize_tags(raw_tags, post.tags, self.pool)
            self._display_tags(normalized)
        except Exception as e:
            self._display_error(self._format_error(e))

    def _display_tags(self, tags: list[str]) -> None:
        self._state = STATE_REVIEWING
        self._stop_spinner()
        self.suggested_tags = tags
        self._existing_checkboxes = []
        self._suggested_checkboxes = []

        container = self.query_one("#suggested-tags-container")
        container.remove_children()

        post = self.posts[self.current_index]

        if post.tags:
            container.mount(Label("Existing:", classes="section-label"))
            for tag in post.tags:
                cb = Checkbox(tag, value=True)
                self._existing_checkboxes.append(cb)
                container.mount(cb)

        if tags:
            container.mount(Label("Suggested:", classes="section-label"))
            existing_lower = {t.lower() for t in self.pool}
            for tag in tags:
                is_new = tag.lower() not in existing_lower
                label = f"✨ {tag}" if is_new else tag
                cb = Checkbox(label, value=True)
                self._suggested_checkboxes.append(cb)
                container.mount(cb)

        self.query_one("#section-header", Label).update("")
        self.query_one("#manual-tags-input", Input).remove_class("hidden")
        self.query_one("#review-buttons").remove_class("hidden")
        self.query_one("#btn-apply", Button).label = "Apply"
        self.query_one("#btn-apply", Button).focus()

    def _apply_tags(self) -> None:
        post = self.posts[self.current_index]

        kept = [cb.label.plain for cb in self._existing_checkboxes if cb.value]
        added = [
            cb.label.plain.removeprefix("✨ ")
            for cb in self._suggested_checkboxes if cb.value
        ]
        removed = [cb.label.plain for cb in self._existing_checkboxes if not cb.value]

        manual_raw = self.query_one("#manual-tags-input", Input).value
        manual = [
            normalize_tag(t, strip_articles=False)
            for t in manual_raw.split(",")
            if t.strip()
        ]
        manual = [t for t in manual if t]

        final_tags = kept + added + manual

        write_tags(post.path, final_tags)
        mark_processed(self.state, post.filename)
        save_state(self.directory, self.state)

        for tag in removed:
            if tag in self.pool:
                self.pool[tag] = max(0, self.pool[tag] - 1)
        for tag in added + manual:
            self.pool[tag] = self.pool.get(tag, 0) + 1

        post.tags = final_tags
        post.metadata["tags"] = final_tags
        post.metadata["lastmod"] = datetime.now().isoformat(timespec="seconds")
        self._stop_spinner(done=True)

        parts = []
        if added:
            parts.append(f"+{len(added)}")
        if manual:
            parts.append(f"+{len(manual)} manual")
        if removed:
            parts.append(f"-{len(removed)}")
        msg = ", ".join(parts) if parts else "no changes"
        self.notify(f"{post.filename}: {msg}")

        self._state = STATE_BROWSING
        self._update_detail_panel()
        self.query_one("#post-table", DataTable).focus()

    # === SUMMARY ===

    def action_summary(self) -> None:
        if self._state != STATE_BROWSING:
            return
        self._state = STATE_LOADING
        post = self.posts[self.current_index]
        self._clear_action_area()
        self._mode = "summary"
        self._start_spinner(self.current_index, "Generating summary...")
        self._call_llm_summary(post)

    @work(exclusive=True)
    async def _call_llm_summary(self, post: Post) -> None:
        try:
            summary = await suggest_summary(
                self.engine, post.metadata, post.content,
            )
            self._display_summary(summary)
        except Exception as e:
            self._display_error(self._format_error(e))

    def _display_summary(self, summary: str) -> None:
        self._state = STATE_REVIEWING
        self._stop_spinner()
        self._suggested_summary = summary

        container = self.query_one("#suggested-tags-container")
        container.remove_children()

        post = self.posts[self.current_index]
        current_desc = post.metadata.get("description", "")

        if current_desc:
            container.mount(Label("[dim]Current:[/dim]", classes="section-label"))
            container.mount(Static(""))
            container.mount(Static(f"[dim]{current_desc} ({len(str(current_desc))} chars)[/dim]"))

        container.mount(Label("Edit summary:", classes="section-label"))
        ta = TextArea(summary, id="summary-editor")
        ta.styles.height = "auto"
        ta.styles.min_height = 3
        ta.styles.max_height = 6
        container.mount(ta)
        container.mount(Static(f"({len(summary)} chars)", id="summary-char-count", classes="meta-desc"))

        self.query_one("#section-header", Label).update("")
        self.query_one("#review-buttons").remove_class("hidden")
        self.query_one("#btn-apply", Button).label = "Apply"
        ta.focus()
        self.query_one("#btn-apply", Button).focus()

    def _apply_summary(self) -> None:
        post = self.posts[self.current_index]

        try:
            ta = self.query_one("#summary-editor", TextArea)
            final_summary = ta.text.strip()
        except Exception:
            final_summary = self._suggested_summary

        if not final_summary:
            self.notify("Summary is empty.", severity="warning")
            return

        write_summary(post.path, final_summary)
        post.metadata["description"] = final_summary
        post.metadata["lastmod"] = datetime.now().isoformat(timespec="seconds")
        self._stop_spinner(done=True)
        self.notify(f"{post.filename}: summary updated")

        self._state = STATE_BROWSING
        self._update_detail_panel()
        self.query_one("#post-table", DataTable).focus()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "summary-editor":
            try:
                counter = self.query_one("#summary-char-count", Static)
                chars = len(event.text_area.text.strip())
                if chars > MAX_SUMMARY_CHARS:
                    counter.update(f"[bold red]({chars} chars — over {MAX_SUMMARY_CHARS})[/bold red]")
                else:
                    counter.update(f"({chars} chars)")
            except Exception:
                pass

    # === INCOMING LINKS ===

    def action_incoming(self) -> None:
        if self._state != STATE_BROWSING:
            return

        self._clear_action_area()
        post = self.posts[self.current_index]
        existing_urls = extract_existing_links(post.content)
        results = self.index.find_similar(
            post,
            n=self.config.links.candidates,
            exclude_urls=existing_urls,
        )
        self._show_incoming(results)
        self.notify(f"{len(results)} potential incoming links found")

    def _show_incoming(self, results: list[dict]) -> None:
        container = self.query_one("#suggested-tags-container")
        container.remove_children()
        header = self.query_one("#section-header", Label)

        if not results:
            header.update("")
            return

        header.update("Possible incoming links:")
        for item in results:
            post_index = self._find_post_index(item["path"])
            if post_index is not None:
                link = ClickableLink(item["title"], post_index)
                link.add_class("incoming-link")
                container.mount(link)
            else:
                container.mount(Static(item["title"]))

    # === OUTGOING LINKS ===

    def action_outgoing(self) -> None:
        if self._state != STATE_BROWSING:
            return

        self._clear_action_area()
        post = self.posts[self.current_index]
        word_count = len(post.content.split())
        budget = min(
            self.config.links.max_per_post,
            math.floor(word_count / self.config.links.words_per_link),
        )

        if budget == 0:
            self.notify("Post too short for link suggestions under current policy.")
            return

        self._state = STATE_LOADING
        self._mode = "outgoing"
        self._start_spinner(self.current_index, "Finding outgoing links...")
        self.query_one("#section-header", Label).update("Querying LLM...")
        self._run_outgoing(post, budget)

    @work(exclusive=True)
    async def _run_outgoing(self, post: Post, budget: int) -> None:
        try:
            existing_urls = extract_existing_links(post.content)
            candidates = self.index.find_similar(
                post,
                n=self.config.links.candidates,
                exclude_urls=existing_urls,
            )

            if not candidates:
                self._stop_spinner()
                self._state = STATE_BROWSING
                self.notify("No similar posts found.")
                self.query_one("#section-header", Label).update("")
                return

            candidates_json = json.dumps([
                {"title": c["title"], "summary": "", "url": c["url"]}
                for c in candidates
            ])

            user_msg = ANCHOR_USER_TEMPLATE.format(
                body=post.content, candidates_json=candidates_json,
            )
            system = ANCHOR_SYSTEM_PROMPT.format(
                max_anchor_words=self.config.links.max_anchor_words,
            )
            prompt = f"{system}\n\n{user_msg}"
            response = await call_llm(self.engine, prompt)

            suggestions = parse_anchor_response(response)

            validated = []
            zones = find_protected_zones(post.content)

            for s in suggestions:
                anchor = s.get("anchor_text", "")
                target = s.get("target_url", "")
                if not anchor or not target:
                    continue

                max_words = self.config.links.max_anchor_words
                if len(anchor.split()) > max_words:
                    continue

                pos = _find_whole_word(post.content, anchor)
                if pos == -1:
                    candidate_info = next(
                        (c for c in candidates if c["url"] == target), None,
                    )
                    if candidate_info:
                        retry_prompt = RETRY_PROMPT.format(
                            anchor_text=anchor,
                            title=candidate_info["title"],
                            url=target,
                            body=post.content,
                        )
                        retry_response = await call_llm(self.engine, retry_prompt)
                        retry_anchor = retry_response.strip().strip('"').strip("'")
                        retry_pos = _find_whole_word(post.content, retry_anchor)
                        if retry_pos != -1 and len(retry_anchor.split()) <= max_words:
                            anchor = retry_anchor
                            pos = retry_pos
                        else:
                            continue
                    else:
                        continue

                if is_in_protected_zone(pos, len(anchor), zones):
                    continue

                validated.append({"anchor_text": anchor, "target_url": target})

            validated = validated[:budget]
            validated = [
                s for s in validated
                if check_anchor_viable(
                    post.content, s["anchor_text"],
                    self.config.links.max_per_paragraph,
                )
            ]

            abs_path = str(post.path.resolve())
            self._session_outgoing[abs_path] = validated

            self._stop_spinner()
            self._state = STATE_REVIEWING if validated else STATE_BROWSING
            self._mode = "outgoing" if validated else ""
            self._show_outgoing(validated)

            if validated:
                self.notify(f"{len(validated)} link suggestions ready")
            else:
                self.index.mark_no_outgoing(post)
                self._mark_table_row(self.current_index, "—")
                self.notify("No natural anchors found for this post.")

        except Exception as e:
            self._stop_spinner()
            self._state = STATE_BROWSING
            self.notify(f"Error: {e}", severity="error")
            self.query_one("#section-header", Label).update("")

    def _show_outgoing(self, suggestions: list[dict]) -> None:
        post = self.posts[self.current_index]
        container = self.query_one("#suggested-tags-container")
        container.remove_children()
        header = self.query_one("#section-header", Label)
        self._outgoing_checkboxes = []

        if not suggestions:
            header.update("")
            self.query_one("#review-buttons").add_class("hidden")
            return

        header.update("Outgoing link suggestions:")
        for item in suggestions:
            anchor = item["anchor_text"]
            url = item["target_url"]
            before, after = self._extract_context(post.content, anchor)
            cb = Checkbox(anchor, value=False)
            self._outgoing_checkboxes.append(cb)
            container.mount(cb)
            context_text = f"[dim]{before}[/dim][bold reverse]{anchor}[/bold reverse][dim]{after}[/dim]"
            container.mount(Static(context_text, classes="outgoing-context"))
            container.mount(Static(f"→ {url}", classes="outgoing-url"))

        self.query_one("#btn-apply", Button).label = "Insert links"
        self.query_one("#review-buttons").remove_class("hidden")

    def _apply_outgoing(self) -> None:
        if not self._outgoing_checkboxes:
            self.notify("No suggestions to apply.")
            return

        post = self.posts[self.current_index]
        abs_path = str(post.path.resolve())
        cached = self._session_outgoing.get(abs_path, [])
        selected = []
        for cb, suggestion in zip(self._outgoing_checkboxes, cached):
            if cb.value:
                selected.append(suggestion)

        if not selected:
            self.notify("No links selected.")
            return

        body, skipped = apply_links(
            post.content, selected,
            max_per_paragraph=self.config.links.max_per_paragraph,
        )

        write_post_with_links(post.path, body)

        post.content = body
        post.metadata["lastmod"] = datetime.now().isoformat(timespec="seconds")
        self.index.update_post(post, self.site.post_url)
        self._build_incoming_index()
        self._session_outgoing.pop(abs_path, None)

        self._stop_spinner(done=True)

        msg = f"{len(selected) - len(skipped)} links applied"
        if skipped:
            msg += f", {len(skipped)} skipped"
        self.notify(f"{post.filename}: {msg}")

        self._state = STATE_BROWSING
        self._update_detail_panel()

    # === LIST LINKS ===

    def action_list_links(self) -> None:
        if self._state != STATE_BROWSING:
            return

        self._clear_action_area()
        post = self.posts[self.current_index]
        links = list_links(post.content)

        if not links:
            self.notify("No links in this post.")
            return

        self._state = STATE_REVIEWING
        self._mode = "list"
        self._show_existing_links(links, post)

    def _show_existing_links(self, links: list[dict], post: Post) -> None:
        container = self.query_one("#suggested-tags-container")
        container.remove_children()
        header = self.query_one("#section-header", Label)
        self._outgoing_checkboxes = []
        self._listed_links = links

        header.update(f"Links ({len(links)}) — check to remove:")
        for link in links:
            anchor = link["anchor_text"]
            url = link["url"]
            before, after = self._extract_context(post.content, f"[{anchor}]({url})")
            cb = Checkbox(anchor, value=False)
            self._outgoing_checkboxes.append(cb)
            container.mount(cb)
            context_text = f"[dim]{before}[/dim][bold reverse]{anchor}[/bold reverse][dim]{after}[/dim]"
            container.mount(Static(context_text, classes="outgoing-context"))
            container.mount(Static(f"→ {url}", classes="outgoing-url"))

        self.query_one("#btn-apply", Button).label = "Remove links"
        self.query_one("#review-buttons").remove_class("hidden")

    def _do_remove_selected(self) -> None:
        post = self.posts[self.current_index]
        urls_to_remove = set()

        for cb, link in zip(self._outgoing_checkboxes, self._listed_links):
            if cb.value:
                urls_to_remove.add(link["url"])

        if not urls_to_remove:
            self.notify("No links selected for removal.")
            return

        body, removed = remove_specific_links(post.content, urls_to_remove)
        write_post_with_links(post.path, body)
        post.content = body
        post.metadata["lastmod"] = datetime.now().isoformat(timespec="seconds")
        self.index.update_post(post, self.site.post_url)
        self._build_incoming_index()

        self._state = STATE_BROWSING
        self._update_detail_panel()
        self.query_one("#post-table", DataTable).focus()
        self.notify(f"{post.filename}: {removed} links removed")

    # === SUGGEST TOPICS ===

    def action_suggest(self) -> None:
        if self._state != STATE_BROWSING:
            return

        self._state = STATE_LOADING
        post = self.posts[self.current_index]
        self._clear_action_area()
        self.query_one("#section-header", Label).update("Querying LLM...")
        self._start_spinner(self.current_index, "Suggesting new topics...")
        self._run_suggest(post)

    @work(exclusive=True)
    async def _run_suggest(self, post: Post) -> None:
        try:
            prompt = SUGGEST_PROMPT.format(
                title=post.metadata.get("title", post.filename),
                content=post.content,
            )
            response = await call_llm(self.engine, prompt)
            suggestions = parse_suggestions(response)

            if not suggestions:
                self._stop_spinner()
                self._state = STATE_BROWSING
                self.notify("No suggestions generated.")
                return

            novel = []
            for title in suggestions:
                if not self._topic_exists(title):
                    novel.append(title)

            self._stop_spinner()
            self._state = STATE_BROWSING
            self._show_suggestions(novel, len(suggestions))

        except Exception as e:
            self._stop_spinner()
            self._state = STATE_BROWSING
            self.notify(f"Error: {e}", severity="error")

    def _topic_exists(self, suggested_title: str, threshold: float = 0.75) -> bool:
        cached = self.index._cache.get("posts", {})
        if not cached:
            return False

        import numpy as np

        suggested_lower = strip_accents(suggested_title.lower())
        for entry in cached.values():
            existing_lower = strip_accents(entry.get("title", "").lower())
            if suggested_lower in existing_lower or existing_lower in suggested_lower:
                return True

        if self.index._model is None:
            return False

        suggested_vec = self.index._encode_single(suggested_title)
        for entry in cached.values():
            other_vec = np.array(entry["embedding"])
            dot = np.dot(suggested_vec, other_vec)
            norm = np.linalg.norm(suggested_vec) * np.linalg.norm(other_vec)
            score = float(dot / norm) if norm > 0 else 0.0
            if score >= threshold:
                return True

        return False

    def _show_suggestions(self, novel: list[str], total: int) -> None:
        container = self.query_one("#suggested-tags-container")
        container.remove_children()
        header = self.query_one("#section-header", Label)

        if not novel:
            header.update("All suggested topics already covered!")
            self.notify(f"{total} topics suggested, all already exist in the blog.")
            return

        filtered = total - len(novel)
        if filtered > 0:
            header.update(f"New post ideas ({filtered} already covered, filtered out):")
        else:
            header.update("New post ideas:")

        self._suggested_topics = novel
        for title in novel:
            container.mount(Static(f"  • {title}"))

        container.mount(Button("Copy to clipboard", id="btn-copy-suggestions"))
        self.notify(f"{len(novel)} new topic ideas")

    @staticmethod
    def _copy_to_clipboard(text: str) -> None:
        import base64
        import sys
        encoded = base64.b64encode(text.encode()).decode()
        sys.stdout.write(f"\033]52;c;{encoded}\a")
        sys.stdout.flush()

    # === EDITOR ===

    def action_editor(self) -> None:
        if self._state != STATE_BROWSING:
            return

        from hugin.tui.editor import EditorScreen

        post = self.posts[self.current_index]

        def on_return(saved: bool) -> None:
            if saved:
                import frontmatter as fm
                updated = fm.load(str(post.path))
                post.metadata = updated.metadata
                post.content = updated.content
                post.tags = list(updated.metadata.get("tags", []) or [])
                post.has_tags = bool(post.tags)
                self.index.update_post(post, self.site.post_url)
                self._build_incoming_index()
                # Refresh title in post list if it changed
                from rich.text import Text
                new_title = post.metadata.get("title", post.filename)
                if post.metadata.get("draft"):
                    cell = Text(f"[DRAFT] {new_title}", style="dim")
                else:
                    cell = Text(new_title)
                table = self.query_one("#post-table", DataTable)
                table.update_cell(self._row_keys[self.current_index], "title", cell)
            self._update_detail_panel()

        self.app.push_screen(
            EditorScreen(post=post),
            on_return,
        )

    # === MANAGE TAGS ===

    def action_manage_tags(self) -> None:
        if self._state != STATE_BROWSING:
            return

        from hugin.tui.tag_manager import TagManagerScreen

        def on_return(_=None) -> None:
            self._update_detail_panel()

        self.app.push_screen(
            TagManagerScreen(
                all_posts=self.all_posts,
                pool=self.pool,
                directory=self.directory,
            ),
            on_return,
        )

    # === CLEAR CACHES ===

    def action_clear_caches(self) -> None:
        if self._state != STATE_BROWSING:
            return

        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                self.index.clear_cache()
                self.app.exit(return_code=42)

        self.app.push_screen(ConfirmClearScreen(), on_confirm)

    # === BUTTON DISPATCH ===

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-apply":
            if self._mode == "tags":
                self._apply_tags()
            elif self._mode == "summary":
                self._apply_summary()
            elif self._mode == "list":
                self._do_remove_selected()
            elif self._mode == "outgoing":
                self._apply_outgoing()
        elif event.button.id == "btn-skip":
            self._state = STATE_BROWSING
            self._update_detail_panel()
            self.query_one("#post-table", DataTable).focus()
        elif event.button.id == "btn-copy-suggestions":
            if self._suggested_topics:
                text = "\n".join(f"• {t}" for t in self._suggested_topics)
                self._copy_to_clipboard(text)
                self.notify(f"{len(self._suggested_topics)} topics copied to clipboard")

    # === ERROR HANDLING ===

    @staticmethod
    def _format_error(e: Exception) -> str:
        import httpx
        if isinstance(e, httpx.ConnectError):
            return f"Connection refused — is the engine running? ({e.request.url})"
        if isinstance(e, httpx.ReadTimeout):
            return "Timeout — model took too long to respond"
        if isinstance(e, httpx.HTTPStatusError):
            code = e.response.status_code
            try:
                body = e.response.json().get("error", {}).get("message", "")
            except Exception:
                body = e.response.text[:200]
            return f"HTTP {code}: {body}"
        if isinstance(e, ValueError):
            return f"Bad LLM response: {e}"
        return f"{type(e).__name__}: {e}"

    def _display_error(self, error: str) -> None:
        self._state = STATE_BROWSING
        self._stop_spinner()
        self.notify(f"Error: {error}", severity="error")

    # === NAVIGATION ===

    def action_back(self) -> None:
        if self._state == STATE_REVIEWING:
            self._stop_spinner()
            self._state = STATE_BROWSING
            self._update_detail_panel()

    def action_quit(self) -> None:
        if self._spinner_timer:
            self._spinner_timer.stop()
        post = self.posts[self.current_index]
        set_last_post(self.state, post.filename)
        save_state(self.directory, self.state)
        self.app.exit()
