"""News → Post Ideas screen.

Flow:
  1. User types a search query.
  2. App fetches Google News RSS headlines.
  3. LLM generates post ideas from the headlines.
  4. User picks ideas via checkboxes → "Create drafts" writes .md files.
"""

import re
import unicodedata
from datetime import datetime
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, LoadingIndicator, Static


def _slugify(title: str) -> str:
    """Convert a title to a URL-friendly slug."""
    s = unicodedata.normalize("NFKD", title.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s.strip())
    s = re.sub(r"-+", "-", s).strip("-")
    return s


class NewsIdeasScreen(Screen):
    """Search news → generate post ideas → create drafts."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "create_drafts", "Create drafts"),
    ]

    DEFAULT_CSS = """
    NewsIdeasScreen {
        background: $surface;
    }

    #search-phase {
        align: center middle;
        height: 1fr;
    }

    #search-box {
        width: 60;
        padding: 2 3;
        border: round $primary;
        background: $panel;
    }

    #search-box Label {
        margin-bottom: 1;
    }

    #search-input {
        margin-bottom: 1;
    }

    #loading-phase {
        align: center middle;
        height: 1fr;
        display: none;
    }

    #loading-status {
        margin-top: 1;
        color: $text-muted;
    }

    #results-phase {
        height: 1fr;
        display: none;
    }

    #results-header {
        padding: 1 2;
        background: $panel;
        border-bottom: solid $primary;
    }

    #ideas-container {
        height: 1fr;
        padding: 1 2;
    }

    .idea-description {
        color: $text-muted;
        padding: 0 4;
        margin-bottom: 1;
    }

    #results-footer {
        height: auto;
        padding: 1 2;
        background: $panel;
        border-top: solid $primary;
        align: right middle;
    }

    #results-footer Button {
        margin-left: 1;
    }
    """

    def __init__(self, engine, directory: Path) -> None:
        super().__init__()
        self.engine = engine
        self.directory = directory
        self._idea_checkboxes: list[tuple[Checkbox, dict]] = []
        self._last_query: str = ""
        self._last_items: list = []
        self._categories: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()

        # Phase 1: search input
        with Vertical(id="search-phase"):
            with Vertical(id="search-box"):
                yield Label("Search term for news:")
                yield Input(placeholder="e.g. gatos, felinos, pets...", id="search-input")
                yield Button("Search", id="btn-search", variant="primary")

        # Phase 2: loading
        with Vertical(id="loading-phase"):
            yield LoadingIndicator()
            yield Label("", id="loading-status")

        # Phase 3: results
        with Vertical(id="results-phase"):
            yield Label("", id="results-header")
            yield ScrollableContainer(id="ideas-container")
            with Horizontal(id="results-footer"):
                yield Button("Create drafts (Ctrl+S)", id="btn-create", variant="primary")
                yield Button("New search", id="btn-new-search")
                yield Button("Cancel", id="btn-cancel")

        yield Footer()

    def on_mount(self) -> None:
        from hugin.hugo import load_categories
        self._categories = load_categories(self.directory)
        self.query_one("#search-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-search":
            self._start_search()
        elif event.button.id == "btn-create":
            self.action_create_drafts()
        elif event.button.id == "btn-new-search":
            self._show_phase("search")
            self.query_one("#search-input", Input).focus()
        elif event.button.id in ("btn-cancel",):
            self.action_cancel()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-input":
            self._start_search()

    def _show_phase(self, phase: str) -> None:
        self.query_one("#search-phase").display = phase == "search"
        self.query_one("#loading-phase").display = phase == "loading"
        self.query_one("#results-phase").display = phase == "results"

    def _set_status(self, text: str) -> None:
        self.query_one("#loading-status", Label).update(text)

    def _start_search(self) -> None:
        query = self.query_one("#search-input", Input).value.strip()
        if not query:
            return
        self._show_phase("loading")
        self._set_status("Fetching news...")
        self._run_search(query)

    @work(exclusive=True)
    async def _run_search(self, query: str) -> None:
        import asyncio
        from hugin.llm import NEWS_IDEAS_PROMPT, call_llm, parse_news_ideas
        from hugin.news import fetch_news

        try:
            # Step 1: fetch news (blocking I/O → thread)
            self._set_status("Fetching news...")
            items = await asyncio.to_thread(fetch_news, query)

            if not items:
                self.notify("No news found for this query.", severity="warning")
                self._show_phase("search")
                return

            # Step 2: ask LLM
            self._set_status(f"Found {len(items)} headlines — generating ideas...")
            news_text = "\n".join(
                f"- {item.title}" + (f": {item.snippet}" if item.snippet else "")
                for item in items
            )
            if self._categories:
                cats_list = ", ".join(f'"{c}"' for c in self._categories)
                categories_instruction = (
                    f"Available categories: {cats_list}\n"
                    f"Assign the single most fitting category to each idea."
                )
                category_field = ', "category": "one of the available categories"'
            else:
                categories_instruction = ""
                category_field = ""

            prompt = NEWS_IDEAS_PROMPT.format(
                query=query,
                news_items=news_text,
                n_ideas=8,
                categories_instruction=categories_instruction,
                category_field=category_field,
            )
            response = await call_llm(self.engine, prompt)
            ideas = parse_news_ideas(response)

            if not ideas:
                self.notify("LLM returned no ideas — try a different query.", severity="warning")
                self._show_phase("search")
                return

            self._last_query = query
            self._last_items = items
            self._show_results(query, ideas)

        except Exception as e:
            self.notify(f"Error: {e}", severity="error")
            self._show_phase("search")

    def _show_results(self, query: str, ideas: list[dict]) -> None:
        container = self.query_one("#ideas-container", ScrollableContainer)
        container.remove_children()
        self._idea_checkboxes = []

        self.query_one("#results-header", Label).update(
            f"Post ideas for [bold]{query}[/bold] — {len(ideas)} suggestions:"
        )

        for idea in ideas:
            cb = Checkbox(idea["title"], value=True)
            self._idea_checkboxes.append((cb, idea))
            container.mount(cb)
            container.mount(Static(idea["description"], classes="idea-description"))

        self._show_phase("results")
        self.query_one("#btn-create", Button).focus()

    def action_create_drafts(self) -> None:
        if not self._idea_checkboxes:
            return
        selected = [idea for cb, idea in self._idea_checkboxes if cb.value]
        if not selected:
            self.notify("No ideas selected.")
            return

        headlines = "\n".join(f"- {item.title}" for item in self._last_items)
        source_comment = (
            f"<!--\nPauta: {{}}\n\n"
            f"Busca: {self._last_query}\n\n"
            f"Notícias relacionadas:\n{headlines}\n-->\n"
        )

        created: list[Path] = []
        now = datetime.now()
        for idea in selected:
            slug = _slugify(idea["title"])
            if not slug:
                continue
            path = self.directory / f"{slug}.md"
            # Avoid overwriting — append suffix if needed
            suffix = 1
            while path.exists():
                path = self.directory / f"{slug}-{suffix}.md"
                suffix += 1
            title = idea["title"].replace('"', "'")
            body = source_comment.format(idea["description"])
            category = idea.get("category", "")
            # Validate against known categories; fall back to first if invalid
            if self._categories and category not in self._categories:
                category = self._categories[0]
            cat_line = f'categories: ["{category}"]\n' if category else ""
            content = (
                f'---\ntitle: "{title}"\n'
                f"date: {now.isoformat(timespec='seconds')}\n"
                f"{cat_line}"
                f"draft: true\n---\n\n"
                f"{body}"
            )
            path.write_text(content)
            created.append(path)

        self.dismiss(created)

    def action_cancel(self) -> None:
        self.dismiss([])
