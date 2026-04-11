"""Post selection screen (--all)."""

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Header, Footer, Label, Static

from hugin.scanner import Post


class SelectionScreen(Screen):
    """Screen to select which posts to process."""

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def __init__(self, posts: list[Post]) -> None:
        super().__init__()
        self.posts = posts
        self._checkboxes: dict[str, Checkbox] = {}

    def compose(self) -> ComposeResult:
        yield Header()

        with Vertical(id="selection-container"):
            yield Label("Select posts to process:", id="selection-title")

            no_tags = [p for p in self.posts if not p.has_tags]
            with_tags = [p for p in self.posts if p.has_tags]

            if no_tags:
                yield Static(f"NO TAGS ({len(no_tags)}):", classes="section-header")
                for post in no_tags:
                    cb = Checkbox(post.filename, value=True, id=f"cb-{post.filename}")
                    self._checkboxes[post.filename] = cb
                    yield cb

            if with_tags:
                yield Static(f"WITH TAGS ({len(with_tags)}):", classes="section-header")
                for post in with_tags:
                    tag_count = len(post.tags)
                    label = f"{post.filename}  ({tag_count} tags)"
                    cb = Checkbox(label, value=False, id=f"cb-{post.filename}")
                    self._checkboxes[post.filename] = cb
                    yield cb

            with Horizontal(id="selection-buttons"):
                yield Button("Process selected", id="btn-process", variant="primary")
                yield Button("Select all", id="btn-select-all")

        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-select-all":
            for cb in self._checkboxes.values():
                cb.value = True

        elif event.button.id == "btn-process":
            selected = [
                post for post in self.posts
                if self._checkboxes.get(post.filename, Checkbox("")).value
            ]
            if not selected:
                self.notify("No posts selected.", severity="warning")
                return

            from hugin.tui.review import ReviewScreen
            self.app.push_screen(ReviewScreen(
                posts=selected,
                engine=self.app.engine,
                pool=self.app.pool,
                state=self.app.state,
                directory=self.app.directory,
                all_posts=self.app.all_posts,
            ))

    def action_quit(self) -> None:
        self.app.exit()
