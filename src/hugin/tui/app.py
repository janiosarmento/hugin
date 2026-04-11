"""Main Textual app."""

from pathlib import Path

from textual.app import App

from hugin.engines import Engine
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
    ) -> None:
        super().__init__()
        self.posts = posts
        self.all_posts = all_posts
        self.engine = engine
        self.pool = pool
        self.state = state
        self.directory = directory

    def on_mount(self) -> None:
        from hugin.tui.review import ReviewScreen
        self.push_screen(ReviewScreen(
            posts=self.posts,
            engine=self.engine,
            pool=self.pool,
            state=self.state,
            directory=self.directory,
            all_posts=self.all_posts,
        ))
