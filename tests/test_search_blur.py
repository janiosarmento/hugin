import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from textual.app import App
from textual.widgets import DataTable, Input

from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
from hugin.engines import Engine
from hugin.scanner import Post
from hugin.tui.review import HuginScreen


def _host(tmp_path):
    posts = []
    for i in range(7):
        path = tmp_path / f"p{i}.md"
        path.write_text("body")
        posts.append(Post(path, {"title": f"Post {i}"}, "body", False,
                          date=datetime.now() - timedelta(days=i + 1), mtime=path.stat().st_mtime))
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


def _selected_title(screen):
    return screen.posts[screen.current_index].metadata["title"]


def test_leaving_search_bar_restores_list_and_keeps_selection(tmp_path):
    async def go():
        app = _host(tmp_path)
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.press("slash", *"post 4")
            await pilot.pause()
            assert len(screen.posts) == 1

            screen.query_one("#post-table", DataTable).focus()
            await pilot.pause()

            assert not screen._search_mode
            assert len(screen.posts) == 7
            assert _selected_title(screen) == "Post 4"
            assert screen.query_one("#search-bar", Input).value == ""

    asyncio.run(go())


def test_clicking_table_during_search_restores_list(tmp_path):
    async def go():
        app = _host(tmp_path)
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.press("slash", *"post 4")
            await pilot.pause()

            await pilot.click("#post-table")
            await pilot.pause()

            assert not screen._search_mode
            assert len(screen.posts) == 7
            assert app.focused is screen.query_one("#post-table", DataTable)

    asyncio.run(go())


def test_enter_and_escape_keep_found_post_selected(tmp_path):
    async def go():
        app = _host(tmp_path)
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            screen = app.screen
            for key in ("enter", "escape"):
                await pilot.press("slash", *"post 4", key)
                await pilot.pause()
                assert len(screen.posts) == 7
                assert _selected_title(screen) == "Post 4"
                assert screen.query_one("#post-table", DataTable).cursor_row == screen.current_index

    asyncio.run(go())


def test_clicking_a_found_row_selects_that_post(tmp_path):
    """The bar blurs on mouse-down, so the list changes under the click."""
    async def go():
        app = _host(tmp_path)
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.press("slash", *"post 4")
            await pilot.pause()

            # Row 0 of the filtered list is Post 4; of the full list, Post 0
            await pilot.click("#post-table", offset=(5, 1))
            await pilot.pause()

            assert len(screen.posts) == 7
            assert _selected_title(screen) == "Post 4"
            assert screen.query_one("#post-table", DataTable).cursor_row == screen.current_index

            # Outside search, clicks move the cursor as usual
            await pilot.click("#post-table", offset=(5, 6))
            await pilot.pause()
            assert _selected_title(screen) == "Post 5"

    asyncio.run(go())
