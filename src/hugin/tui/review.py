"""Unified Hugin screen — tags, summaries, links, and editing."""

import json
import math
import re
from datetime import datetime
from pathlib import Path

from textual import work
from textual.binding import Binding
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
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

from hugin.affiliates import load_affiliates
from hugin.config import HuginConfig
from hugin.embeddings import EmbeddingIndex
from hugin.tui.post_picker import PostPickerScreen
from hugin.engines import Engine, load_engines, save_last_engine
from hugin.hugo import HugoSite
from hugin.linker import (
    _find_whole_word,
    _is_affiliate_url,
    apply_links,
    check_anchor_viable,
    extract_existing_links,
    find_keyword_anchors,
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
    RERANK_SYSTEM,
    RERANK_USER_TEMPLATE,
    LINK_KEYWORDS_SYSTEM,
    LINK_KEYWORDS_USER_TEMPLATE,
    RETRY_PROMPT,
    SUGGEST_PROMPT,
    call_llm,
    parse_anchor_response,
    parse_rerank_response,
    parse_suggestions,
    suggest_summary,
    suggest_tags,
)
from hugin.normalizer import normalize_tag, normalize_tags, strip_accents
from hugin.scanner import Post, format_pool_for_prompt
from hugin.project import ProjectConfig, load_project
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


class ConfirmGitSyncScreen(ModalScreen[bool]):
    """Confirm git sync (pull + push)."""

    BINDINGS = [
        ("y", "yes", "Yes"),
        ("n", "no", "No"),
        ("escape", "no", "No"),
    ]

    DEFAULT_CSS = """
    ConfirmGitSyncScreen {
        align: center middle;
    }

    #gitsync-modal {
        width: 52;
        height: auto;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }

    #gitsync-modal Label {
        text-style: bold;
        margin-bottom: 1;
    }

    #gitsync-buttons {
        height: auto;
        margin-top: 1;
    }

    #gitsync-buttons Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="gitsync-modal"):
            yield Label("Sync repository with GitHub?")
            yield Static("Will commit local changes, pull --rebase, then push.")
            with Horizontal(id="gitsync-buttons"):
                yield Button("Yes", id="btn-yes", variant="primary")
                yield Button("No", id="btn-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class GitSyncResultScreen(ModalScreen):
    """Shows the result of a git sync operation."""

    BINDINGS = [("escape", "close", "Close"), ("enter", "close", "Close")]

    DEFAULT_CSS = """
    GitSyncResultScreen {
        align: center middle;
    }

    #gitresult-modal {
        width: 70;
        height: auto;
        max-height: 80%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }

    #gitresult-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #gitresult-output {
        margin-bottom: 1;
        color: $text-muted;
    }

    #gitresult-close {
        margin-top: 1;
    }
    """

    def __init__(self, success: bool, output: str, needs_reload: bool = False) -> None:
        super().__init__()
        self._success = success
        self._output = output
        self._needs_reload = needs_reload

    def compose(self) -> ComposeResult:
        title = "Sync complete" if self._success else "Sync failed"
        with Vertical(id="gitresult-modal"):
            yield Label(title, id="gitresult-title")
            yield Static(self._output or "(no output)", id="gitresult-output")
            if self._needs_reload:
                yield Static("App will reload to pick up new posts.")
            yield Button("Close", id="btn-close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(self._needs_reload)

    def action_close(self) -> None:
        self.dismiss(self._needs_reload)


class NewPostScreen(ModalScreen[str | None]):
    """Ask for a filename and create a new blank post."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("enter", "confirm", "Create"),
    ]

    DEFAULT_CSS = """
    NewPostScreen {
        align: center middle;
    }

    #newpost-modal {
        width: 60;
        height: auto;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }

    #newpost-modal Label {
        text-style: bold;
        margin-bottom: 1;
    }

    #newpost-input {
        margin-bottom: 1;
    }

    #newpost-buttons {
        height: auto;
        margin-top: 1;
    }

    #newpost-buttons Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="newpost-modal"):
            yield Label("New post — enter filename:")
            yield Input(placeholder="my-new-post.md", id="newpost-input")
            with Horizontal(id="newpost-buttons"):
                yield Button("Create", id="btn-create", variant="primary")
                yield Button("Cancel", id="btn-cancel-new")

    def on_mount(self) -> None:
        self.query_one("#newpost-input", Input).focus()

    def _get_filename(self) -> str:
        name = self.query_one("#newpost-input", Input).value.strip()
        if name and not name.endswith(".md"):
            name += ".md"
        return name

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-create":
            self.action_confirm()
        else:
            self.dismiss(None)

    def action_confirm(self) -> None:
        name = self._get_filename()
        if name:
            self.dismiss(name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class LoadingScreen(ModalScreen):
    """Non-interactive modal with animated spinner and status message."""

    DEFAULT_CSS = """
    LoadingScreen {
        align: center middle;
    }

    #loading-box {
        width: 60;
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

    def set_message(self, message: str) -> None:
        self._message = message

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
        Binding("q", "quit", "Quit", show=False),
        Binding("escape", "back", "Back", show=False),
        ("t", "tags", "Tags"),
        ("m", "manage_tags", "Mngr"),
        ("s", "summary", "Summ"),
        ("i", "incoming", "In"),
        ("o", "outgoing", "Out"),
        ("d", "direct_links", "Direct"),
        ("z", "amazon", "Amzn"),
        ("l", "list_links", "List"),
        ("b", "broken_links", "Broken"),
        ("u", "suggest", "Sugg"),
        ("e", "editor", "Edit"),
        ("n", "pick_engine", "Engine"),
        ("c", "clear_caches", "Clr"),
        ("p", "new_post", "Post"),
        ("w", "news_ideas", "News"),
        ("r", "redirects", "Redirs"),
        ("X", "delete_post", "Delete"),
        ("g", "git_sync", "Git"),
        ("comma", "project_settings", "Sett"),
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

    #btn-copy-post {
        margin-top: 1;
        margin-bottom: 1;
    }

    #review-buttons {
        height: auto;
        margin-top: 1;
    }

    .hidden {
        display: none;
    }

    #search-bar {
        height: 3;
        border: tall transparent;
        background: $panel;
    }

    #search-bar:focus {
        border: tall $accent;
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
        self._spinning_row: int | None = None
        self._done_rows: set[int] = set()
        self._state = STATE_BROWSING
        self._mode = ""
        self._suggested_summary = ""
        self._suggested_topics: list[str] = []
        self._incoming_index: dict[str, int] = {}
        self._session_outgoing: dict[str, list[dict]] = {}
        self._loading_screen: LoadingScreen | None = None
        self._project = load_project(directory)
        self._search_mode: bool = False
        self._search_base: list = []  # full self.posts saved during search

    @property
    def _words_per_link(self) -> int:
        """Effective words-per-link: project override or global default."""
        wpl = self._project.links.words_per_link
        return wpl if wpl > 0 else self.config.links.words_per_link

    def compose(self) -> ComposeResult:
        yield Static(self.BANNER, id="banner")

        with Horizontal(id="review-container"):
            with Vertical(id="post-list-panel"):
                yield Input(placeholder="/ to search", id="search-bar")
                table = DataTable(id="post-table", cursor_type="row", zebra_stripes=True)
                yield table

            with Vertical(id="detail-panel"):
                yield Static("", id="engine-label")
                yield Label(id="progress-label")
                yield Static("", id="post-meta")
                yield Button("Copy .md to clipboard", id="btn-copy-post")
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
        table.focus()

    # --- Incoming index ---

    def _build_incoming_index(self) -> None:
        counts: dict[str, int] = {}
        for post in self.all_posts:
            for url in extract_existing_links(post.content):
                counts[url] = counts.get(url, 0) + 1
        self._incoming_index = counts

    # --- Loading overlay ---

    def _start_spinner(self, index: int, message: str = "Processing...") -> None:
        self._spinning_row = index
        self._loading_screen = LoadingScreen(message)
        self.app.push_screen(self._loading_screen)

    def _stop_spinner(self, done: bool = False) -> None:
        if self._spinning_row is not None and done:
            table = self.query_one("#post-table", DataTable)
            table.update_cell(self._row_keys[self._spinning_row], "status", "✓")
            self._done_rows.add(self._spinning_row)
        self._spinning_row = None
        if self._loading_screen is not None:
            try:
                self._loading_screen.dismiss()
            except Exception:
                pass
            self._loading_screen = None

    def _set_spinner_message(self, message: str) -> None:
        if self._loading_screen is not None:
            self._loading_screen.set_message(message)

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

        self.app.push_screen(EnginePickerScreen(engines, self.engine.id, self.engine.model), on_pick)

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
        existing_links = extract_existing_links(post.content)
        outgoing_count = len(existing_links)
        affiliate_count = sum(1 for url in existing_links if _is_affiliate_url(url))
        word_count = len(post.content.split())
        budget = min(
            self.config.links.max_per_post,
            math.floor(word_count / self._words_per_link),
        )
        post_url = self.index.get_post_url(post)
        incoming_count = self._incoming_index.get(post_url, 0) if post_url else 0

        affiliate_label = f" ({affiliate_count} aff)" if affiliate_count else ""
        table.add_row("links out", f"{outgoing_count}/{budget}{affiliate_label}")
        table.add_row("links in", str(incoming_count))

        if budget == 0:
            table.add_row("outgoing", "[bold red]too short[/bold red]")
        elif self.index.has_no_outgoing(post):
            table.add_row("outgoing", "[dim]no opportunities[/dim]")

        link_keywords = self.index.get_link_keywords(post)
        if link_keywords:
            table.add_row("link profile", link_keywords)

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
        self.query_one("#btn-apply", Button).variant = "primary"

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
                words=self._project.summary.words,
                style=self._project.summary.style,
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
            math.floor(word_count / self._words_per_link),
        )

        if budget == 0:
            self.index.mark_no_outgoing(post)
            self._mark_table_row(self.current_index, "—")
            self.notify("Post too short for link suggestions under current policy.")
            return

        self._state = STATE_LOADING
        self._mode = "outgoing"
        self._start_spinner(self.current_index, "Finding outgoing links...")
        self.query_one("#section-header", Label).update("Querying LLM...")
        self._run_outgoing(post, budget)

    def action_direct_links(self) -> None:
        """Open post picker for manual link target selection."""
        if self._state != STATE_BROWSING:
            return

        post = self.posts[self.current_index]
        word_count = len(post.content.split())
        budget = min(
            self.config.links.max_per_post,
            math.floor(word_count / self._words_per_link),
        )

        if budget == 0:
            self.notify("Post too short for link suggestions under current policy.")
            return

        def on_pick(selected: list[dict] | None) -> None:
            if not selected:
                return
            self._clear_action_area()
            self._state = STATE_LOADING
            self._mode = "outgoing"
            self._start_spinner(self.current_index, "Finding anchors...")
            self.query_one("#section-header", Label).update("Querying LLM...")
            self._run_outgoing_manual(post, budget, selected)

        url_fn = lambda meta, fname: self.site.post_url(meta, fname)
        self.app.push_screen(
            PostPickerScreen(self.all_posts, post, url_fn),
            on_pick,
        )

    def action_amazon(self) -> None:
        """Insert affiliate links from the keyword dictionary."""
        if self._state != STATE_BROWSING:
            return

        affiliates = load_affiliates()
        if not affiliates:
            self.notify("No affiliates configured. Edit ~/.hugin/affiliates.toml")
            return

        post = self.posts[self.current_index]
        existing_urls = extract_existing_links(post.content)
        existing_normalized = {u.rstrip("/") for u in existing_urls}
        zones = find_protected_zones(post.content)

        # Find dictionary keywords that appear in the body
        matches = []
        for keyword, url in affiliates.items():
            if url.rstrip("/") in existing_normalized:
                continue
            pos = _find_whole_word(post.content, keyword)
            if pos != -1 and not is_in_protected_zone(pos, len(keyword), zones):
                matches.append({"anchor_text": keyword, "target_url": url})

        if not matches:
            self.notify("No affiliate keyword matches in this post.")
            return

        self._clear_action_area()
        abs_path = str(post.path.resolve())
        for m in matches:
            m["_manual"] = True
        self._session_outgoing[abs_path] = matches
        self._state = STATE_REVIEWING
        self._mode = "outgoing"
        self._show_outgoing(matches)
        self.notify(f"{len(matches)} affiliate matches found")

    async def _find_anchors(
        self, post: Post, candidates: list[dict], existing_urls: set[str],
    ) -> list[dict]:
        """Ask LLM to find anchor text in the post body for each candidate.

        Returns validated suggestions with anchor_text and target_url.
        """
        existing_normalized = {u.rstrip("/") for u in existing_urls}
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
        response = await call_llm(self.engine, user_msg, system=system)

        suggestions = parse_anchor_response(response)
        zones = find_protected_zones(post.content)
        candidate_urls = {c["url"] for c in candidates}
        max_words = self.config.links.max_anchor_words
        validated = []

        for i, s in enumerate(suggestions, 1):
            if len(suggestions) > 1:
                self._set_spinner_message(
                    f"Step 3/3 — Validating anchor {i}/{len(suggestions)}..."
                )
            anchor = s.get("anchor_text", "")
            target = s.get("target_url", "")
            if not anchor or not target:
                continue
            if target not in candidate_urls:
                continue
            if target.rstrip("/") in existing_normalized:
                continue
            if len(anchor.split()) > max_words:
                continue

            pos = _find_whole_word(post.content, anchor)
            if pos == -1:
                # Retry: ask LLM for a different anchor
                candidate_info = next(
                    (c for c in candidates if c["url"] == target), None,
                )
                if not candidate_info:
                    continue
                retry_response = await call_llm(self.engine, RETRY_PROMPT.format(
                    anchor_text=anchor, title=candidate_info["title"],
                    url=target, body=post.content,
                ))
                anchor = retry_response.strip().strip('"').strip("'")
                pos = _find_whole_word(post.content, anchor)
                if pos == -1 or len(anchor.split()) > max_words:
                    continue

            if is_in_protected_zone(pos, len(anchor), zones):
                continue

            validated.append({"anchor_text": anchor, "target_url": target})

        return validated

    def _finish_outgoing(
        self, post: Post, validated: list[dict], mark_empty: bool = False,
    ) -> None:
        """Store results and update UI after anchor finding completes."""
        abs_path = str(post.path.resolve())
        self._session_outgoing[abs_path] = validated

        self._stop_spinner()
        self._state = STATE_REVIEWING if validated else STATE_BROWSING
        self._mode = "outgoing" if validated else ""
        self._show_outgoing(validated)

        if validated:
            self.notify(f"{len(validated)} link suggestions ready")
        elif mark_empty:
            self.index.mark_no_outgoing(post)
            self._mark_table_row(self.current_index, "—")
            self.notify("No natural anchors found for this post.")
        else:
            self.notify("No natural anchors found for selected posts.")

    @work(exclusive=True)
    async def _run_outgoing_manual(
        self, post: Post, budget: int, candidates: list[dict],
    ) -> None:
        """Find anchors for manually selected candidates."""
        try:
            existing_urls = extract_existing_links(post.content)
            self._set_spinner_message(f"Finding anchors for {len(candidates)} post(s)...")
            validated = await self._find_anchors(post, candidates, existing_urls)

            # Keyword anchors as fallback for candidates the LLM missed
            llm_urls = {s["target_url"] for s in validated}
            uncovered = [c for c in candidates if c["url"] not in llm_urls]
            fallback = find_keyword_anchors(post.content, uncovered)
            existing_normalized = {u.rstrip("/") for u in existing_urls}
            fallback = [
                s for s in fallback
                if s["target_url"].rstrip("/") not in existing_normalized
            ]
            for s in fallback:
                s["_manual"] = True

            validated = (validated + fallback)[:budget]
            self._finish_outgoing(post, validated)

        except Exception as e:
            self._stop_spinner()
            self._state = STATE_BROWSING
            self.notify(f"Error: {e}", severity="error")

    @work(exclusive=True)
    async def _run_outgoing(self, post: Post, budget: int) -> None:
        """Find outgoing links automatically: embed + tags → rerank → anchor + keyword fallback."""
        try:
            # Step 0: always regenerate link profile on explicit trigger
            self._set_spinner_message("Step 1/4 — Building link profile...")
            kw_prompt = LINK_KEYWORDS_USER_TEMPLATE.format(
                title=post.metadata.get("title", post.filename),
                content=post.content[:3000],
            )
            keywords = (await call_llm(self.engine, kw_prompt, system=LINK_KEYWORDS_SYSTEM)).strip()
            self.index.set_link_keywords(post, self.site.post_url, keywords)
            total_steps = 4

            self._set_spinner_message(f"Step {total_steps - 2}/{total_steps} — Searching similar posts...")
            existing_urls = extract_existing_links(post.content)
            existing_normalized = {u.rstrip("/") for u in existing_urls}
            pre_filter_n = max(self.config.links.candidates * 4, 20)

            # Pool 1: semantic similarity
            semantic = self.index.find_similar(
                post, n=pre_filter_n, exclude_urls=existing_urls,
            )

            # Pool 2: shared-tag candidates (≥2 tags)
            tag_based = self.index.find_by_shared_tags(
                post, n=pre_filter_n // 2, exclude_urls=existing_urls,
            )

            # Merge pools — deduplicate by URL, keep highest score
            seen: dict[str, dict] = {}
            for c in semantic + tag_based:
                url = c["url"]
                if url not in seen or c["score"] > seen[url]["score"]:
                    seen[url] = c
            candidates = sorted(seen.values(), key=lambda x: x["score"], reverse=True)

            if not candidates:
                self._stop_spinner()
                self._state = STATE_BROWSING
                self.index.mark_no_outgoing(post)
                self._mark_table_row(self.current_index, "—")
                self.notify("No similar posts found.")
                self.query_one("#section-header", Label).update("")
                return

            # LLM reranking — inclusive mode
            self._set_spinner_message(f"Step {total_steps - 1}/{total_steps} — Reranking {len(candidates)} candidates...")
            rerank_json = json.dumps([
                {"title": c["title"], "url": c["url"]} for c in candidates
            ])
            rerank_prompt = RERANK_USER_TEMPLATE.format(
                title=post.metadata.get("title", post.filename),
                body=post.content[:2000],
                candidates_json=rerank_json,
            )
            rerank_response = await call_llm(self.engine, rerank_prompt, system=RERANK_SYSTEM)
            relevant_urls = set(parse_rerank_response(rerank_response))

            # Keep: reranked + mention-boosted; guarantee a minimum of 3 pass through
            reranked = [
                c for c in candidates
                if c["url"] in relevant_urls or c.get("score", 0) > 1.0
            ]
            min_candidates = max(3, self.config.links.candidates // 2)
            if len(reranked) < min_candidates:
                # Supplement with highest-score candidates not yet included
                included_urls = {c["url"] for c in reranked}
                extras = [c for c in candidates if c["url"] not in included_urls]
                reranked += extras[:min_candidates - len(reranked)]
            candidates = reranked[:self.config.links.candidates]

            if not candidates:
                self._stop_spinner()
                self._state = STATE_BROWSING
                self.index.mark_no_outgoing(post)
                self._mark_table_row(self.current_index, "—")
                self.notify("No relevant posts found after reranking.")
                self.query_one("#section-header", Label).update("")
                return

            # LLM anchor finding
            self._set_spinner_message(f"Step {total_steps}/{total_steps} — Finding anchors for {len(candidates)} posts...")
            validated = await self._find_anchors(post, candidates, existing_urls)
            validated = [
                s for s in validated
                if check_anchor_viable(
                    post.content, s["anchor_text"],
                    self.config.links.max_per_paragraph,
                )
            ]

            # Keyword fallback for candidates the LLM missed
            llm_urls = {s["target_url"] for s in validated}
            uncovered = [c for c in candidates if c["url"] not in llm_urls]
            if uncovered:
                keyword_suggestions = find_keyword_anchors(post.content, uncovered)
                keyword_suggestions = [
                    s for s in keyword_suggestions
                    if s["target_url"].rstrip("/") not in existing_normalized
                    and s["target_url"] not in llm_urls
                ]
                validated = validated + keyword_suggestions

            validated = validated[:budget]
            self._finish_outgoing(post, validated, mark_empty=True)

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
        url_to_title = {
            entry["url"]: entry.get("title", "")
            for entry in self.index._cache.get("posts", {}).values()
        }
        for item in suggestions:
            anchor = item["anchor_text"]
            url = item["target_url"]
            before, after = self._extract_context(post.content, anchor)
            cb = Checkbox(anchor, value=False)
            self._outgoing_checkboxes.append(cb)
            container.mount(cb)
            context_text = f"[dim]{before}[/dim][bold reverse]{anchor}[/bold reverse][dim]{after}[/dim]"
            container.mount(Static(context_text, classes="outgoing-context"))
            title = url_to_title.get(url, "") or url_to_title.get(url.rstrip("/") + "/", "")
            dest = f"→ {url}"
            if title:
                dest += f"\n  [dim]{title}[/dim]"
            container.mount(Static(dest, classes="outgoing-url"))

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

        # Manual/affiliate picks bypass paragraph limits
        is_manual = any(
            s.get("_manual") for s in cached
        )
        body, skipped = apply_links(
            post.content, selected,
            max_per_paragraph=0 if is_manual else self.config.links.max_per_paragraph,
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

        btn = self.query_one("#btn-apply", Button)
        btn.label = "Remove links"
        btn.variant = "error"
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

    def action_broken_links(self) -> None:
        if self._state != STATE_BROWSING:
            return

        from hugin.tui.broken_links import BrokenLinksScreen

        def on_return(changed: bool) -> None:
            if changed:
                self._build_incoming_index()
                self._update_detail_panel()

        self.app.push_screen(
            BrokenLinksScreen(all_posts=self.all_posts, site=self.site),
            on_return,
        )

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

    def _copy_to_clipboard(self, text: str) -> None:
        self.app.copy_to_clipboard(text)

    # === EDITOR ===

    def action_editor(self) -> None:
        if self._state != STATE_BROWSING:
            return
        self._open_editor_for_post(self.posts[self.current_index], self.current_index)

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

    # === NEW POST ===

    def action_new_post(self) -> None:
        if self._state != STATE_BROWSING:
            return

        def on_filename(filename: str | None) -> None:
            if not filename:
                return
            path = self.directory / filename
            if path.exists():
                self.notify(f"{filename} already exists", severity="error")
                return
            # Write minimal frontmatter
            now = datetime.now()
            title = path.stem
            content = f"---\ntitle: {title}\ndate: {now.isoformat(timespec='seconds')}\ndraft: true\n---\n"
            path.write_text(content)
            # Build Post object
            import frontmatter as fm
            loaded = fm.load(str(path))
            post = Post(
                path=path,
                metadata=loaded.metadata,
                content=loaded.content,
                has_tags=False,
                tags=[],
                date=now,
            )
            # Insert at top of list and rebuild table to keep index consistent
            self.posts.insert(0, post)
            self.all_posts.insert(0, post)
            self._rebuild_post_table()   # sets current_index=0, moves cursor, updates panel
            self._open_editor_for_post(post, index=0)

        self.app.push_screen(NewPostScreen(), on_filename)

    def action_news_ideas(self) -> None:
        """Open the news → post ideas screen."""
        if self._state != STATE_BROWSING:
            return

        from hugin.tui.news_ideas import NewsIdeasScreen

        def on_created(paths: list) -> None:
            if not paths:
                return
            import frontmatter as fm
            from rich.text import Text

            table = self.query_one("#post-table", DataTable)
            now = datetime.now()
            first_new_index = len(self.posts)
            for path in paths:
                try:
                    loaded = fm.load(str(path))
                except Exception:
                    continue
                post = Post(
                    path=path,
                    metadata=loaded.metadata,
                    content=loaded.content,
                    has_tags=False,
                    tags=[],
                    date=now,
                )
                self.posts.append(post)
                self.all_posts.append(post)
                title = loaded.metadata.get("title", path.stem)
                row_key = f"news-{path.name}"
                table.add_row("—", Text(f"[DRAFT] {title}", style="dim"), key=row_key)
                self._row_keys.append(row_key)

            self.current_index = first_new_index
            table.move_cursor(row=first_new_index)
            self._update_detail_panel()
            self.notify(f"{len(paths)} draft(s) created")

        self.app.push_screen(
            NewsIdeasScreen(engine=self.engine, directory=self.directory),
            on_created,
        )

    # === REDIRECTS ===

    def action_redirects(self) -> None:
        """Open the redirects manager screen."""
        if self._state != STATE_BROWSING:
            return
        from hugin.redirects import find_redirects_file
        from hugin.tui.redirects_screen import RedirectsScreen

        path = find_redirects_file(self.directory)
        if path is None:
            self.notify("No static/ directory found — cannot locate _redirects.", severity="warning")
            return

        # Create the file if it doesn't exist yet
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()

        self.app.push_screen(RedirectsScreen(path))

    # === DELETE POST ===

    def action_delete_post(self) -> None:
        """Delete the current post and optionally add a redirect."""
        if self._state != STATE_BROWSING or self._search_mode:
            return

        post = self.posts[self.current_index]
        post_url = self.index.get_post_url(post)

        # Collect similar posts BEFORE removing from index
        from hugin.linker import extract_existing_links
        existing_urls = extract_existing_links(post.content)
        candidates = self.index.find_similar(post, n=3, exclude_urls=existing_urls)

        from hugin.tui.redirects_screen import ConfirmDeleteScreen, RedirectSuggestScreen

        def on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return

            # Delete the file
            try:
                post.path.unlink()
            except Exception as e:
                self.notify(f"Error deleting file: {e}", severity="error")
                return

            # Remove from embedding index
            self.index.remove_post(post)

            # Remove from in-memory lists
            idx = self.current_index
            row_key = self._row_keys[idx]
            self.posts.pop(idx)
            self._row_keys.pop(idx)
            for i, p in enumerate(self.all_posts):
                if p.path == post.path:
                    self.all_posts.pop(i)
                    break

            # Remove from DataTable and update cursor
            table = self.query_one("#post-table", DataTable)
            table.remove_row(row_key)

            filename = post.filename
            if self.posts:
                self.current_index = min(idx, len(self.posts) - 1)
                table.move_cursor(row=self.current_index)
                self._update_detail_panel()
            else:
                self.current_index = 0
                self._clear_action_area()
                self.query_one("#progress-label", Label).update("No posts")

            self.notify(f"Deleted: {filename}")

            # Offer redirect only if we know the URL
            if not post_url:
                return

            from hugin.redirects import append_redirect, find_redirects_file
            redirects_path = find_redirects_file(self.directory)
            if redirects_path is None:
                return

            def on_redirect(dest: str | None) -> None:
                if not dest:
                    return
                append_redirect(redirects_path, post_url, dest)
                self.notify(f"Redirect: {post_url} → {dest}")

            self.app.push_screen(
                RedirectSuggestScreen(origin=post_url, candidates=candidates),
                on_redirect,
            )

        self.app.push_screen(
            ConfirmDeleteScreen(
                title=post.metadata.get("title", post.filename),
                url=post_url or "",
            ),
            on_confirm,
        )

    def _open_editor_for_post(self, post: Post, index: int) -> None:
        from hugin.tui.editor import EditorScreen

        def on_return(saved: bool) -> None:
            if saved:
                import frontmatter as fm
                from rich.text import Text
                updated = fm.load(str(post.path))
                post.metadata = updated.metadata
                post.content = updated.content
                post.tags = list(updated.metadata.get("tags", []) or [])
                post.has_tags = bool(post.tags)
                self.index.update_post(post, self.site.post_url)
                self._build_incoming_index()
                new_title = post.metadata.get("title", post.filename)
                cell = Text(f"[DRAFT] {new_title}", style="dim") if post.metadata.get("draft") else Text(new_title)
                table = self.query_one("#post-table", DataTable)
                table.update_cell(self._row_keys[index], "title", cell)
            self._update_detail_panel()

        self.app.push_screen(EditorScreen(post=post), on_return)

    # === PROJECT SETTINGS ===

    def action_project_settings(self) -> None:
        if self._state != STATE_BROWSING:
            return

        from hugin.tui.project_settings import ProjectSettingsScreen

        def on_return(saved: bool) -> None:
            if saved:
                self.notify("Project settings saved")

        self.app.push_screen(
            ProjectSettingsScreen(self._project, self.directory, self.config.links.words_per_link),
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
        elif event.button.id == "btn-copy-post":
            post = self.posts[self.current_index]
            text = post.path.read_text()
            self._copy_to_clipboard(text)
            self.notify(f"{post.filename} copied to clipboard")

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

    def action_git_sync(self) -> None:
        if self._state != STATE_BROWSING:
            return

        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                self._start_spinner(self.current_index, "Syncing with GitHub...")
                self._do_git_sync()

        self.app.push_screen(ConfirmGitSyncScreen(), on_confirm)

    @work(thread=True)
    def _do_git_sync(self) -> None:
        from hugin.git import git_sync

        result = git_sync(self.directory)
        success = result.success
        needs_reload = result.needs_reload
        output = result.output
        self.app.call_from_thread(self._stop_spinner)

        def show_result() -> None:
            def on_close(reload: bool) -> None:
                if reload:
                    self.app.exit(return_code=42)

            self.app.push_screen(GitSyncResultScreen(success, output, needs_reload), on_close)

        self.app.call_from_thread(show_result)

    # === SEARCH ===

    def on_key(self, event) -> None:
        """Intercept '/' to activate search when the table has focus."""
        if (
            event.key == "slash"
            and self._state == STATE_BROWSING
            and not self._search_mode
        ):
            event.stop()
            self._search_mode = True
            self._search_base = list(self.posts)
            bar = self.query_one("#search-bar", Input)
            bar.value = ""
            bar.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search-bar":
            return
        # Strip leading slash that leaks from the activation key
        if event.value.startswith("/"):
            event.input.value = event.value[1:]
            return  # on_input_changed fires again with clean value
        query = event.value.strip().lower()
        # Activate search mode if the user typed directly into the bar
        if query and not self._search_mode:
            self._search_mode = True
            self._search_base = list(self.posts)
        if self._search_mode and query:
            filtered = [
                p for p in self._search_base
                if query in p.filename.lower()
                or query in str(p.metadata.get("title", "")).lower()
            ]
        else:
            filtered = list(self._search_base) if self._search_mode else list(self.posts)
        self.posts = filtered
        self._rebuild_post_table()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-bar":
            self._close_search()

    def _close_search(self) -> None:
        """Exit search mode: restore full list, cursor on selected post."""
        if not self._search_mode:
            return
        selected_post = self.posts[self.current_index] if self.posts else None
        self.posts = self._search_base
        self._search_base = []
        self._search_mode = False
        bar = self.query_one("#search-bar", Input)
        bar.value = ""
        self._rebuild_post_table()
        if selected_post:
            for i, p in enumerate(self.posts):
                if p.path == selected_post.path:
                    self.current_index = i
                    self.query_one("#post-table", DataTable).move_cursor(row=i)
                    break
        self._update_detail_panel()
        self.query_one("#post-table", DataTable).focus()

    def _rebuild_post_table(self) -> None:
        """Clear and repopulate the post table from self.posts."""
        from rich.text import Text
        table = self.query_one("#post-table", DataTable)
        table.clear()
        self._row_keys = []
        for i, post in enumerate(self.posts):
            title = post.metadata.get("title", post.filename)
            cell = Text(f"[DRAFT] {title}", style="dim") if post.metadata.get("draft") else Text(title)
            status = "—" if self.index.has_no_outgoing(post) else " "
            key = f"search-{i}-{post.filename}"
            table.add_row(status, cell, key=key)
            self._row_keys.append(key)
        # Reset cursor to first row
        self.current_index = 0
        if self.posts:
            table.move_cursor(row=0)
            self._update_detail_panel()

    def action_back(self) -> None:
        if self._search_mode:
            self._close_search()
            return
        if self._state == STATE_LOADING:
            self.workers.cancel_all()
            self._stop_spinner()
            self._state = STATE_BROWSING
            self._mode = ""
            self.query_one("#section-header", Label).update("")
            self.notify("Cancelled")
        elif self._state == STATE_REVIEWING:
            self._stop_spinner()
            self._state = STATE_BROWSING
            self._update_detail_panel()

    def action_quit(self) -> None:
        post = self.posts[self.current_index]
        set_last_post(self.state, post.filename)
        save_state(self.directory, self.state)
        self.app.exit()
