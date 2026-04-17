"""Munin TUI application."""

import json
import math
import re
from datetime import datetime
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Label,
    Static,
)

from hugin.engines import Engine, load_engines, save_last_engine
from hugin.llm import call_llm
from hugin.scanner import Post

from munin.config import MuninConfig
from munin.embeddings import EmbeddingIndex
from munin.hugo import HugoSite
from munin.linker import (
    apply_links,
    check_anchor_viable,
    extract_existing_links,
    find_protected_zones,
    is_in_protected_zone,
    write_post_with_links,
)
from munin.state import SessionState

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


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


STATE_BROWSING = "browsing"
STATE_LOADING = "loading"
STATE_REVIEWING = "reviewing"

# --- LLM prompts ---

ANCHOR_SYSTEM_PROMPT = """\
You are a technical blog editor. Your task is to identify natural anchor text within a blog post body that could serve as an internal link to related posts.

Rules:
- The anchor_text must appear verbatim in the post body.
- Prefer specific technical terms, tool names, or concepts over generic phrases.
- Do not suggest anchors inside headings, code blocks, inline code, images, or existing links.
- Suggest at most one anchor per candidate post.
- Omit candidates for which no natural anchor exists — do not force one.
- Return a JSON array and nothing else. No preamble, no markdown fences."""

ANCHOR_USER_TEMPLATE = """\
Post body:
{body}

Candidate posts (suggest an anchor for each where natural):
{candidates_json}

Return format:
[{{"target_url": "/posts/foo/", "anchor_text": "exact phrase from body"}}]"""

SUGGEST_PROMPT = """\
You are a blog content strategist. Based on the following blog post, suggest 5 to 10 topics \
for NEW posts that would complement this one. These should be topics that a reader of this \
post would naturally want to read next.

RULES:
- Each suggestion should be a specific, actionable post title
- Titles must be in the same language as the post content
- Be specific — not "more about X" but a concrete angle or question
- Return a JSON array of strings, nothing else

POST TITLE: {title}

POST CONTENT:
{content}

Return format: ["Post title 1", "Post title 2", ...]"""

RETRY_PROMPT = """\
The phrase '{anchor_text}' does not appear verbatim in the post body.
Choose a phrase from the body that exists exactly as written and would
naturally link to: {title} ({url})

Post body:
{body}"""


class MuninScreen(Screen):
    """Main Munin screen with post list and link suggestions."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("i", "incoming", "Incoming"),
        ("o", "outgoing", "Outgoing"),
        ("s", "suggest", "Suggest"),
        ("e", "pick_engine", "Engine"),
        ("c", "clear_caches", "Clear"),
        ("escape", "back", "Back"),
    ]

    DEFAULT_CSS = """
    #munin-container {
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

    #outgoing-container Checkbox {
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

    #outgoing-buttons {
        height: auto;
        margin-top: 1;
    }

    .hidden {
        display: none;
    }
    """

    def __init__(
        self,
        posts: list[Post],
        all_posts: list[Post],
        engine: Engine,
        config: MuninConfig,
        site: HugoSite,
        index: EmbeddingIndex,
    ) -> None:
        super().__init__()
        self.posts = posts
        self.all_posts = all_posts
        self.engine = engine
        self.config = config
        self.site = site
        self.index = index
        self.current_index = 0
        self._row_keys: list[str] = []
        self._spinner_frame = 0
        self._spinning_row: int | None = None
        self._spinner_timer: Timer | None = None
        self._state = STATE_BROWSING
        self._session = SessionState()
        self._outgoing_checkboxes: list[Checkbox] = []
        self._suggested_topics: list[str] = []
        self._incoming_index: dict[str, int] = {}  # url → count of posts linking to it

    def compose(self) -> ComposeResult:
        banner = Static(
            " __  __           _\n"
            "|  \\/  |_  _ _ _ (_)_ _\n"
            "| |\\/| | || | ' \\| | ' \\\n"
            "|_|  |_|\\_,_|_||_|_|_||_|",
            id="banner",
        )
        yield banner

        with Horizontal(id="munin-container"):
            with Vertical(id="post-list-panel"):
                table = DataTable(
                    id="post-table", cursor_type="row", zebra_stripes=True,
                )
                yield table

            with Vertical(id="detail-panel"):
                yield Static("", id="engine-label")
                yield Label(id="progress-label")
                yield Static("", id="post-meta")
                yield Label("", classes="section-label", id="incoming-header")
                yield Vertical(id="incoming-container")
                yield Label("", classes="section-label", id="outgoing-header")
                yield Vertical(id="outgoing-container")
                with Horizontal(id="outgoing-buttons", classes="hidden"):
                    yield Button("Apply", id="btn-apply", variant="primary")
                    yield Button("Skip", id="btn-skip")
                yield Label("", classes="section-label", id="suggest-header")
                yield Vertical(id="suggest-container")

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
        self._update_detail_panel()

    def _build_incoming_index(self) -> None:
        """Pre-compute how many posts link to each URL."""
        counts: dict[str, int] = {}
        for post in self.all_posts:
            for url in extract_existing_links(post.content):
                counts[url] = counts.get(url, 0) + 1
        self._incoming_index = counts

    # --- Spinner ---

    def _tick_spinner(self) -> None:
        if self._spinning_row is not None:
            self._spinner_frame += 1
            char = SPINNER_FRAMES[self._spinner_frame % len(SPINNER_FRAMES)]
            table = self.query_one("#post-table", DataTable)
            table.update_cell(
                self._row_keys[self._spinning_row], "status", char,
            )

    def _start_spinner(self, index: int) -> None:
        self._spinning_row = index
        self._spinner_frame = 0

    def _stop_spinner(self, done: bool = False) -> None:
        if self._spinning_row is not None:
            table = self.query_one("#post-table", DataTable)
            table.update_cell(
                self._row_keys[self._spinning_row],
                "status",
                "✓" if done else " ",
            )
            self._spinning_row = None

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
        if event.cursor_row is not None and event.cursor_row != self.current_index:
            self.current_index = event.cursor_row
            self._state = STATE_BROWSING
            self._update_detail_panel()

    def _update_detail_panel(self) -> None:
        from rich.table import Table

        post = self.posts[self.current_index]
        total = len(self.posts)

        self.query_one("#progress-label", Label).update(
            f"{self.current_index + 1}/{total} — {post.filename}"
        )

        # Metadata table
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
            if value is None or value == "":
                continue
            if isinstance(value, list):
                table.add_row(key, ", ".join(str(v) for v in value))
            else:
                table.add_row(key, str(value))

        desc = meta.get("description", "")
        if desc:
            table.add_row("description", f"{desc} ({len(str(desc))} chars)")
        tags = meta.get("tags")
        if tags:
            table.add_row("tags", ", ".join(str(t) for t in tags))

        # Link counts
        outgoing_count = len(extract_existing_links(post.content))
        post_url = self.index.get_post_url(post)
        incoming_count = self._incoming_index.get(post_url, 0) if post_url else 0

        table.add_row("links out", str(outgoing_count))
        table.add_row("links in", str(incoming_count))

        # Link budget warning
        word_count = len(post.content.split())
        budget = min(
            self.config.links.max_per_post,
            math.floor(word_count / self.config.links.words_per_link),
        )
        if budget == 0:
            table.add_row("outgoing", "[bold red]too short[/bold red]")
        elif self.index.has_no_outgoing(post):
            table.add_row("outgoing", "[dim]no opportunities[/dim]")

        self.query_one("#post-meta", Static).update(table)

        # Clean panels when navigating — cached results stay in session
        # but the UI starts clean each time
        self._clear_panels()

    def _clear_panels(self) -> None:
        """Clear incoming, outgoing, and suggest panels."""
        self.query_one("#incoming-container").remove_children()
        self.query_one("#incoming-header", Label).update("")
        self.query_one("#outgoing-container").remove_children()
        self.query_one("#outgoing-header", Label).update("")
        self.query_one("#outgoing-buttons").add_class("hidden")
        self._outgoing_checkboxes = []
        self.query_one("#suggest-container").remove_children()
        self.query_one("#suggest-header", Label).update("")

    def _show_incoming(self, results: list[dict]) -> None:
        container = self.query_one("#incoming-container")
        container.remove_children()
        header = self.query_one("#incoming-header", Label)

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

    def _find_post_index(self, abs_path: str) -> int | None:
        """Find the index of a post in the list by absolute path."""
        for i, post in enumerate(self.posts):
            if str(post.path.resolve()) == abs_path:
                return i
        return None

    def _show_outgoing(self, suggestions: list[dict]) -> None:
        post = self.posts[self.current_index]
        container = self.query_one("#outgoing-container")
        container.remove_children()
        header = self.query_one("#outgoing-header", Label)
        self._outgoing_checkboxes = []

        if not suggestions:
            header.update("")
            self.query_one("#outgoing-buttons").add_class("hidden")
            return

        header.update("Outgoing link suggestions:")
        for item in suggestions:
            anchor = item["anchor_text"]
            url = item["target_url"]
            before, after = self._extract_context(post.content, anchor)
            cb = Checkbox(anchor, value=True)
            self._outgoing_checkboxes.append(cb)
            container.mount(cb)
            context_text = f"[dim]{before}[/dim][bold reverse]{anchor}[/bold reverse][dim]{after}[/dim]"
            container.mount(Static(context_text, classes="outgoing-context"))
            container.mount(Static(f"→ {url}", classes="outgoing-url"))

        self.query_one("#outgoing-buttons").remove_class("hidden")

    @staticmethod
    def _extract_context(content: str, anchor: str, chars: int = 80) -> tuple[str, str]:
        """Extract text before and after an anchor for context display."""
        pos = content.find(anchor)
        if pos == -1:
            return ("", "")

        start = max(0, pos - chars)
        end = min(len(content), pos + len(anchor) + chars)

        before = content[start:pos]
        after = content[pos + len(anchor):end]

        # Trim to word boundaries
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

        # Clean whitespace
        before = before.replace("\n", " ")
        after = after.replace("\n", " ")

        return (before, after)

    # --- Suggest ---

    def action_suggest(self) -> None:
        if self._state != STATE_BROWSING:
            return

        self._state = STATE_LOADING
        post = self.posts[self.current_index]
        self._clear_panels()
        self._start_spinner(self.current_index)
        self._run_suggest(post)

    @work(exclusive=True)
    async def _run_suggest(self, post: Post) -> None:
        try:
            prompt = SUGGEST_PROMPT.format(
                title=post.metadata.get("title", post.filename),
                content=post.content,
            )
            response = await call_llm(self.engine, prompt)

            # Parse JSON array of titles
            suggestions = self._parse_suggestions(response)

            if not suggestions:
                self._stop_spinner()
                self._state = STATE_BROWSING
                self.notify("No suggestions generated.")
                return

            # Filter out topics that already exist (via embedding similarity)
            novel = []
            for title in suggestions:
                # Check if a very similar post already exists
                # Encode the suggestion and find similar posts
                if not self._topic_exists(title):
                    novel.append(title)

            self._stop_spinner()
            self._state = STATE_BROWSING
            self._show_suggestions(novel, len(suggestions))

        except Exception as e:
            self._stop_spinner()
            self._state = STATE_BROWSING
            self.notify(f"Error: {e}", severity="error")

    def _parse_suggestions(self, text: str) -> list[str]:
        text = text.strip()
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()

        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                result = json.loads(text[start:end + 1])
                if isinstance(result, list):
                    return [str(item) for item in result]
            except json.JSONDecodeError:
                pass
        return []

    def _topic_exists(self, suggested_title: str, threshold: float = 0.75) -> bool:
        """Check if a topic already exists in the blog by embedding similarity."""
        cached = self._cache_posts()
        if not cached:
            return False

        import numpy as np
        from hugin.normalizer import strip_accents

        # Quick text match first
        suggested_lower = strip_accents(suggested_title.lower())
        for entry in cached.values():
            existing_lower = strip_accents(entry.get("title", "").lower())
            if suggested_lower in existing_lower or existing_lower in suggested_lower:
                return True

        # Embedding similarity check
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

    def _cache_posts(self) -> dict:
        return self.index._cache.get("posts", {})

    def _show_suggestions(self, novel: list[str], total: int) -> None:
        container = self.query_one("#suggest-container")
        container.remove_children()
        header = self.query_one("#suggest-header", Label)

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

    # --- Incoming ---

    def action_incoming(self) -> None:
        if self._state != STATE_BROWSING:
            return

        post = self.posts[self.current_index]
        existing_urls = extract_existing_links(post.content)
        results = self.index.find_similar(
            post,
            n=self.config.links.candidates,
            exclude_urls=existing_urls,
        )

        abs_path = str(post.path.resolve())
        self._session.set_incoming(abs_path, results)
        self._show_incoming(results)
        self.notify(f"{len(results)} potential incoming links found")

    # --- Outgoing ---

    def action_outgoing(self) -> None:
        if self._state != STATE_BROWSING:
            return

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
        self._start_spinner(self.current_index)

        # Clear previous outgoing
        container = self.query_one("#outgoing-container")
        container.remove_children()
        self.query_one("#outgoing-header", Label).update("Querying LLM...")
        self._outgoing_checkboxes = []

        self._run_outgoing(post, budget)

    @work(exclusive=True)
    async def _run_outgoing(self, post: Post, budget: int) -> None:
        try:
            # Step 1: Candidate discovery
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
                self.query_one("#outgoing-header", Label).update("")
                return

            # Step 2: LLM anchor detection
            candidates_json = json.dumps([
                {
                    "title": c["title"],
                    "summary": "",  # We don't have per-post summary in candidates
                    "url": c["url"],
                }
                for c in candidates
            ])

            user_msg = ANCHOR_USER_TEMPLATE.format(
                body=post.content,
                candidates_json=candidates_json,
            )

            # Build messages for chat completion
            prompt = f"{ANCHOR_SYSTEM_PROMPT}\n\n{user_msg}"
            response = await call_llm(self.engine, prompt)

            # Parse response
            suggestions = self._parse_anchor_response(response)

            # Step 3: Validate anchors
            validated = []
            zones = find_protected_zones(post.content)

            for s in suggestions:
                anchor = s.get("anchor_text", "")
                target = s.get("target_url", "")

                if not anchor or not target:
                    continue

                # Verbatim check
                if anchor not in post.content:
                    # Retry once
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
                        if retry_anchor in post.content:
                            anchor = retry_anchor
                        else:
                            continue
                    else:
                        continue

                # Protected zone check
                pos = post.content.find(anchor)
                if pos != -1 and is_in_protected_zone(pos, len(anchor), zones):
                    continue

                validated.append({"anchor_text": anchor, "target_url": target})

            # Step 4: Budget enforcement
            validated = validated[:budget]

            # Step 5: Filter out anchors in saturated paragraphs
            validated = [
                s for s in validated
                if check_anchor_viable(
                    post.content,
                    s["anchor_text"],
                    self.config.links.max_per_paragraph,
                )
            ]

            # Store and display
            abs_path = str(post.path.resolve())
            self._session.set_outgoing(abs_path, validated)

            self._stop_spinner()
            self._state = STATE_REVIEWING if validated else STATE_BROWSING
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
            self.query_one("#outgoing-header", Label).update("")

    def _parse_anchor_response(self, text: str) -> list[dict]:
        """Parse LLM response into list of anchor suggestions."""
        text = text.strip()
        # Strip code fences
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()

        # Extract JSON array
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                result = json.loads(text[start:end + 1])
                if isinstance(result, list):
                    return result
            except json.JSONDecodeError:
                pass

        return []

    # --- Apply ---

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-apply":
            self._do_apply()
        elif event.button.id == "btn-skip":
            self._clear_panels()
            self._state = STATE_BROWSING
            self.query_one("#post-table", DataTable).focus()
        elif event.button.id == "btn-copy-suggestions":
            if hasattr(self, "_suggested_topics") and self._suggested_topics:
                text = "\n".join(f"• {t}" for t in self._suggested_topics)
                import subprocess
                subprocess.run(["pbcopy"], input=text.encode(), check=True)
                self.notify(f"{len(self._suggested_topics)} topics copied to clipboard")

    def action_navigate(self, index: int) -> None:
        """Navigate to a post by index (triggered by incoming link click)."""
        self._navigate_to_post(index)

    def _navigate_to_post(self, index: int) -> None:
        """Navigate to a post by index, updating table cursor and detail panel."""
        if 0 <= index < len(self.posts):
            self.current_index = index
            self._state = STATE_BROWSING
            table = self.query_one("#post-table", DataTable)
            table.move_cursor(row=index)
            self._update_detail_panel()

    def _do_apply(self) -> None:
        if not self._outgoing_checkboxes:
            self.notify("No suggestions to apply.")
            return

        post = self.posts[self.current_index]

        # Collect checked items
        abs_path = str(post.path.resolve())
        cached = self._session.get_outgoing(abs_path) or []
        selected = []
        for cb, suggestion in zip(self._outgoing_checkboxes, cached):
            if cb.value:
                selected.append(suggestion)

        if not selected:
            self.notify("No links selected.")
            return

        # Apply links
        body, skipped = apply_links(
            post.content,
            selected,
            max_per_paragraph=self.config.links.max_per_paragraph,
        )

        write_post_with_links(post.path, body)

        # Update in-memory state
        post.content = body
        post.metadata["lastmod"] = datetime.now().isoformat(timespec="seconds")
        self.index.update_post(post, self.site.post_url)
        self._build_incoming_index()
        self._session.clear_outgoing(abs_path)

        self._stop_spinner(done=True)

        msg = f"{len(selected) - len(skipped)} links applied"
        if skipped:
            msg += f", {len(skipped)} skipped"
        self.notify(f"{post.filename}: {msg}")

        self._state = STATE_BROWSING
        self._update_detail_panel()

    # --- Back ---

    def action_clear_caches(self) -> None:
        if self._state != STATE_BROWSING:
            return

        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                self.index.clear_cache()
                self.app.exit(return_code=42)  # magic code to signal restart

        self.app.push_screen(ConfirmClearScreen(), on_confirm)

    def action_back(self) -> None:
        if self._state == STATE_REVIEWING:
            self._state = STATE_BROWSING

    def action_quit(self) -> None:
        if self._spinner_timer:
            self._spinner_timer.stop()
        self.app.exit()


class MuninApp(App):
    """Munin main application."""

    TITLE = "Munin"

    CSS = """
    #banner {
        height: auto;
        content-align: left middle;
        text-style: bold;
        color: $accent;
        padding: 0 1;
        margin-bottom: 1;
    }
    """

    def __init__(
        self,
        posts: list[Post],
        all_posts: list[Post],
        engine: Engine,
        config: MuninConfig,
        site: HugoSite,
        index: EmbeddingIndex,
    ) -> None:
        super().__init__()
        self.posts = posts
        self.all_posts = all_posts
        self.engine = engine
        self.config = config
        self.site = site
        self.index = index

    def on_mount(self) -> None:
        self.push_screen(MuninScreen(
            posts=self.posts,
            all_posts=self.all_posts,
            engine=self.engine,
            config=self.config,
            site=self.site,
            index=self.index,
        ))
