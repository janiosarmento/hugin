"""Main Textual app."""

import json
from pathlib import Path

from textual.app import App

from hugin.config import HuginConfig
from hugin.embeddings import EmbeddingIndex
from hugin.engines import CONFIG_DIR
from hugin.engines import Engine
from hugin.fsutil import atomic_write_text
from hugin.hugo import HugoSite
from hugin.log import log_notification
from hugin.scanner import Post

THEME_FILE = CONFIG_DIR / "theme.json"


def _load_theme() -> str | None:
    try:
        data = json.loads(THEME_FILE.read_text(encoding="utf-8"))
        return data.get("theme")
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def _save_theme(name: str) -> None:
    atomic_write_text(THEME_FILE, json.dumps({"theme": name}))


class HuginApp(App):
    """hugin main app."""

    TITLE = "hugin"

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
        startup_warnings: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.startup_warnings = startup_warnings or []
        self.posts = posts
        self.all_posts = all_posts
        self.engine = engine
        self.pool = pool
        self.state = state
        self.directory = directory
        self.config = config
        self.site = site
        self.index = index
        saved = _load_theme()
        if saved and saved in self.available_themes:
            self.theme = saved

    def notify(self, message, *, title="", severity="information", timeout=None, markup=True):
        """Show a notification; warnings and errors are also kept in the log."""
        log_notification(f"{title}: {message}" if title else str(message), severity)
        super().notify(message, title=title, severity=severity, timeout=timeout, markup=markup)

    def watch_theme(self, theme_name: str) -> None:
        _save_theme(theme_name)

    def on_mount(self) -> None:
        from hugin.tui.review import HuginScreen
        self.push_screen(HuginScreen(
            posts=self.posts,
            all_posts=self.all_posts,
            engine=self.engine,
            pool=self.pool,
            state=self.state,
            directory=self.directory,
            config=self.config,
            site=self.site,
            index=self.index,
        ))
        if self.startup_warnings:
            shown = "\n".join(self.startup_warnings[:5])
            more = len(self.startup_warnings) - 5
            if more > 0:
                shown += f"\n…and {more} more"
            self.notify(
                f"{len(self.startup_warnings)} file(s) left out of the list:\n{shown}",
                severity="warning",
                timeout=20,
            )
