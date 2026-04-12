"""Tag review screen."""

from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
)

from hugin.engines import Engine, load_engines, save_last_engine
from hugin.llm import suggest_summary, suggest_tags
from hugin.normalizer import normalize_tag, normalize_tags
from hugin.scanner import Post, format_pool_for_prompt
from hugin.state import mark_processed, save_state
from hugin.writer import write_summary, write_tags

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# UI states
STATE_BROWSING = "browsing"
STATE_LOADING = "loading"
STATE_REVIEWING = "reviewing"


class ReviewScreen(Screen):
    """Tag review screen with post list and LLM suggestions."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("t", "tags", "Tags"),
        ("s", "summary", "Summary"),
        ("m", "manage_tags", "Manage"),
        ("e", "pick_engine", "Engine"),
        ("v", "open_vim", ""),
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

    #tags-panel {
        width: 2fr;
        padding: 1;
        overflow-y: auto;
    }

    #tags-panel > * {
        height: auto;
    }

    #suggested-tags-container {
        height: auto;
    }

    #review-buttons {
        height: auto;
        margin-top: 1;
    }

    .section-label {
        margin-top: 1;
        text-style: bold;
    }

    #post-meta {
        margin-top: 1;
        margin-bottom: 1;
        height: auto;
    }

    #engine-label {
        color: $text-muted;
        margin-bottom: 1;
    }

    #manual-tags-input {
        margin-top: 1;
    }

    .hidden {
        display: none;
    }
    """

    def __init__(
        self,
        posts: list[Post],
        engine: Engine,
        pool: dict[str, int],
        state: dict,
        directory: Path,
        all_posts: list[Post],
    ) -> None:
        super().__init__()
        self.posts = posts
        self.engine = engine
        self.pool = pool
        self.state = state
        self.directory = directory
        self.all_posts = all_posts
        self.current_index = 0
        self.suggested_tags: list[str] = []
        self._existing_checkboxes: list[Checkbox] = []
        self._suggested_checkboxes: list[Checkbox] = []
        self._row_keys: list[str] = []
        self._spinner_frame = 0
        self._spinning_row: int | None = None
        self._done_rows: set[int] = set()
        self._spinner_timer: Timer | None = None
        self._state = STATE_BROWSING
        self._mode = ""  # "tags" or "summary"
        self._suggested_summary = ""

    BANNER = """\
  _  _           _
 | || |_  _ __ _(_)_ _
 | __ | || / _` | | ' \\
 |_||_|\\_,_\\__, |_|_||_|
           |___/"""

    def compose(self) -> ComposeResult:
        yield Static(self.BANNER, id="banner")

        with Horizontal(id="review-container"):
            with Vertical(id="post-list-panel"):
                table = DataTable(id="post-table", cursor_type="row", zebra_stripes=True)
                yield table

            with Vertical(id="tags-panel"):
                yield Static("", id="engine-label")
                yield Label(id="progress-label")
                yield Static("", id="post-meta")
                yield Label("", classes="section-label", id="suggested-header")
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
            row_key = table.add_row(" ", cell, key=f"post-{i}")
            self._row_keys.append(f"post-{i}")

        self._spinner_timer = self.set_interval(0.08, self._tick_spinner)
        self._update_engine_label()
        self._update_right_panel()

    def _tick_spinner(self) -> None:
        if self._spinning_row is not None:
            self._spinner_frame += 1
            char = SPINNER_FRAMES[self._spinner_frame % len(SPINNER_FRAMES)]
            table = self.query_one("#post-table", DataTable)
            table.update_cell(self._row_keys[self._spinning_row], "status", char)

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

    def action_open_vim(self) -> None:
        if self._state != STATE_BROWSING:
            return
        import subprocess
        post = self.posts[self.current_index]
        with self.app.suspend():
            subprocess.call(["vim", str(post.path)])
        # Reload frontmatter after edit
        import frontmatter
        updated = frontmatter.load(str(post.path))
        post.metadata = updated.metadata
        post.content = updated.content
        post.tags = list(updated.metadata.get("tags", []) or [])
        post.has_tags = bool(post.tags)
        self._update_right_panel()

    def action_manage_tags(self) -> None:
        if self._state != STATE_BROWSING:
            return

        from hugin.tui.tag_manager import TagManagerScreen

        def on_return(_=None) -> None:
            self._update_right_panel()

        self.app.push_screen(
            TagManagerScreen(
                all_posts=self.all_posts,
                pool=self.pool,
                directory=self.directory,
            ),
            on_return,
        )

    def _start_spinner(self, index: int) -> None:
        self._spinning_row = index
        self._spinner_frame = 0

    def _stop_spinner(self, done: bool = False) -> None:
        if self._spinning_row is not None:
            table = self.query_one("#post-table", DataTable)
            if done:
                table.update_cell(self._row_keys[self._spinning_row], "status", "✓")
                self._done_rows.add(self._spinning_row)
            else:
                table.update_cell(self._row_keys[self._spinning_row], "status", " ")
            self._spinning_row = None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self._state != STATE_BROWSING:
            return
        if event.cursor_row is not None and event.cursor_row != self.current_index:
            self.current_index = event.cursor_row
            self._update_right_panel()

    def _update_right_panel(self) -> None:
        from rich.table import Table

        post = self.posts[self.current_index]
        total = len(self.posts)

        progress = self.query_one("#progress-label", Label)
        progress.update(f"{self.current_index + 1}/{total} — {post.filename}")

        # Build Rich table with all frontmatter metadata
        # description and tags always last, in that order
        meta = post.metadata

        # Match Textual DataTable zebra stripe colors
        css_vars = self.app.get_css_variables()
        accent = self.app.current_theme.accent if hasattr(self.app, "current_theme") else "cyan"
        stripe_bg = css_vars.get("surface-darken-1", "#2d2d2d")

        table = Table(
            show_header=False,
            box=None,
            padding=(0, 1, 0, 0),
            row_styles=["", f"on {stripe_bg}"],
        )
        table.add_column("Field", style=f"bold {accent}", no_wrap=True)
        table.add_column("Value")

        last_keys = ("description", "tags")
        for key, value in meta.items():
            if key in last_keys:
                continue
            row = self._format_meta_value(value)
            if row is not None:
                table.add_row(key, row)

        # description penultimate, tags last
        desc = meta.get("description", "")
        if desc:
            table.add_row("description", f"{desc} ({len(str(desc))} chars)")
        tags = meta.get("tags")
        if tags:
            table.add_row("tags", ", ".join(str(t) for t in tags))
        elif tags is not None:
            table.add_row("tags", "(none)")

        self.query_one("#post-meta", Static).update(table)

        self._clear_suggestions()

    @staticmethod
    def _format_meta_value(value) -> str | None:
        if value is None or value == "":
            return None
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return str(value)

    def _clear_suggestions(self) -> None:
        self.suggested_tags = []
        self._suggested_summary = ""
        self._existing_checkboxes = []
        self._suggested_checkboxes = []
        self._mode = ""
        container = self.query_one("#suggested-tags-container")
        container.remove_children()

        header = self.query_one("#suggested-header", Label)
        header.update("")

        manual_input = self.query_one("#manual-tags-input", Input)
        manual_input.value = ""
        manual_input.add_class("hidden")
        self.query_one("#review-buttons").add_class("hidden")

    def action_tags(self) -> None:
        if self._state != STATE_BROWSING:
            return

        self._state = STATE_LOADING
        self._mode = "tags"
        post = self.posts[self.current_index]

        self._clear_suggestions()
        self._mode = "tags"

        self._start_spinner(self.current_index)
        self._call_llm_tags(post)

    def action_summary(self) -> None:
        if self._state != STATE_BROWSING:
            return

        self._state = STATE_LOADING
        self._mode = "summary"
        post = self.posts[self.current_index]

        self._clear_suggestions()
        self._mode = "summary"

        self._start_spinner(self.current_index)
        self._call_llm_summary(post)

    def action_back(self) -> None:
        if self._state == STATE_REVIEWING:
            self._stop_spinner()
            self._clear_suggestions()
            self._state = STATE_BROWSING

    # --- LLM calls ---

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

    @work(exclusive=True)
    async def _call_llm_summary(self, post: Post) -> None:
        try:
            summary = await suggest_summary(
                self.engine, post.metadata, post.content,
            )
            self._display_summary(summary)
        except Exception as e:
            self._display_error(self._format_error(e))

    # --- Display results ---

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

        header = self.query_one("#suggested-header", Label)
        header.update("")

        self.query_one("#manual-tags-input", Input).remove_class("hidden")
        self.query_one("#review-buttons").remove_class("hidden")
        self.query_one("#btn-apply", Button).focus()

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
            container.mount(Static(f"[dim]{current_desc}[/dim]"))

        container.mount(Label("Suggested:", classes="section-label"))
        container.mount(Static(""))
        container.mount(Static(summary))
        container.mount(Static(f"({len(summary)} chars)", classes="meta-desc"))

        header = self.query_one("#suggested-header", Label)
        header.update("")

        self.query_one("#review-buttons").remove_class("hidden")
        self.query_one("#btn-apply", Button).focus()

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

    # --- Apply actions ---

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-apply":
            if self._mode == "tags":
                self._apply_tags()
            elif self._mode == "summary":
                self._apply_summary()
        elif event.button.id == "btn-skip":
            self._back_to_list()

    def _apply_tags(self) -> None:
        post = self.posts[self.current_index]

        kept = [cb.label.plain for cb in self._existing_checkboxes if cb.value]
        added = [
            cb.label.plain.removeprefix("✨ ")
            for cb in self._suggested_checkboxes if cb.value
        ]
        removed = [cb.label.plain for cb in self._existing_checkboxes if not cb.value]

        # Manual tags from input
        manual_raw = self.query_one("#manual-tags-input", Input).value
        manual = [
            normalize_tag(t)
            for t in manual_raw.split(",")
            if t.strip()
        ]
        manual = [t for t in manual if t]  # filter empty after normalization

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

        self._clear_suggestions()
        self._state = STATE_BROWSING
        self._update_right_panel()
        self.query_one("#post-table", DataTable).focus()

    def _apply_summary(self) -> None:
        post = self.posts[self.current_index]

        write_summary(post.path, self._suggested_summary)
        post.metadata["description"] = self._suggested_summary
        self._stop_spinner(done=True)
        self.notify(f"{post.filename}: summary updated")

        self._clear_suggestions()
        self._state = STATE_BROWSING
        self._update_right_panel()
        self.query_one("#post-table", DataTable).focus()

    def _back_to_list(self) -> None:
        self._clear_suggestions()
        self._state = STATE_BROWSING
        self.query_one("#post-table", DataTable).focus()

    def action_quit(self) -> None:
        if self._spinner_timer:
            self._spinner_timer.stop()
        self.app.exit()
