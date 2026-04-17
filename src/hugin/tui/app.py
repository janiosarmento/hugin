"""Main Textual app."""

from pathlib import Path

from textual.app import App

from hugin.config import HuginConfig
from hugin.embeddings import EmbeddingIndex
from hugin.engines import Engine
from hugin.hugo import HugoSite
from hugin.scanner import Post


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
