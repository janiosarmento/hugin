"""Post list sorting (S) and the date columns on the main screen."""

import asyncio
from unittest.mock import MagicMock

import frontmatter
from textual.app import App
from textual.widgets import DataTable

from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
from hugin.engines import Engine
from hugin.scanner import Post
from hugin.tui.review import HuginScreen

# (title, published, modified, draft) — given in the default order
ROWS = [
    ("Draft", None, "2026-03-05T10:00:00", True),
    ("Newest", "2026-03-01T10:00:00", "2026-03-01T10:00:00", False),
    ("Middle", "2026-02-01T10:00:00", "2026-03-10T10:00:00", False),
    ("Oldest", "2026-01-01T10:00:00", None, False),
]


def _host(tmp_path):
    posts = []
    for i, (title, date, lastmod, draft) in enumerate(ROWS):
        meta = {"title": title}
        if date:
            meta["date"] = date
        if lastmod:
            meta["lastmod"] = lastmod
        if draft:
            meta["draft"] = True
        path = tmp_path / f"p{i}.md"
        path.write_text(frontmatter.dumps(frontmatter.Post("Body", **meta)) + "\n")
        posts.append(Post(path, meta, "Body", False, mtime=path.stat().st_mtime))
    site = MagicMock()
    site.post_url.return_value = "/x"
    site.warnings = []
    index = MagicMock()
    index.has_no_outgoing.return_value = False
    index.get_post_url.return_value = "/x"
    index.get_link_keywords.return_value = ""
    index.link_keywords_for_path.return_value = ""

    class Host(App):
        def on_mount(self):
            self.push_screen(HuginScreen(
                posts=posts, all_posts=list(posts),
                engine=Engine("t", "http://localhost/v1", "m", 30, None),
                pool={}, state={}, directory=tmp_path,
                config=HuginConfig(LinksConfig(), EmbeddingsConfig(), FrontmatterConfig()),
                site=site, index=index,
            ))

    return Host()


def _titles(screen):
    return [p.metadata["title"] for p in screen.posts]


def test_sort_cycles_and_keeps_selection(tmp_path):
    async def go():
        app = _host(tmp_path)
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen._navigate_to_post(3)  # Oldest
            await pilot.pause()

            await pilot.press("S")
            await pilot.pause()
            assert _titles(screen) == ["Newest", "Middle", "Oldest", "Draft"]
            assert screen.posts[screen.current_index].metadata["title"] == "Oldest"
            table = screen.query_one("#post-table", DataTable)
            assert table.cursor_row == screen.current_index

            await pilot.press("S")
            await pilot.pause()
            assert _titles(screen) == ["Middle", "Draft", "Newest", "Oldest"]

            await pilot.press("S")
            await pilot.pause()
            assert _titles(screen) == ["Draft", "Newest", "Middle", "Oldest"]

    asyncio.run(go())


def test_grid_shows_published_and_modified_days(tmp_path):
    async def go():
        app = _host(tmp_path)
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            table = app.screen.query_one("#post-table", DataTable)
            assert table.get_row_at(2)[2:] == ["2026-02-01", "2026-03-10"]
            assert table.get_row_at(0)[2:] == ["", "2026-03-05"]

    asyncio.run(go())

