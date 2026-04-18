"""Main Textual app."""

import json
from pathlib import Path

from textual.app import App

from hugin.config import HuginConfig
from hugin.embeddings import EmbeddingIndex
from hugin.engines import CONFIG_DIR
from hugin.engines import Engine
from hugin.hugo import HugoSite
from hugin.scanner import Post

THEME_FILE = CONFIG_DIR / "theme.json"


def _load_theme() -> str | None:
    try:
        data = json.loads(THEME_FILE.read_text())
        return data.get("theme")
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def _save_theme(name: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    THEME_FILE.write_text(json.dumps({"theme": name}))


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
        saved = _load_theme()
        if saved and saved in self.available_themes:
            self.theme = saved

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
