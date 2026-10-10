import asyncio
from unittest.mock import MagicMock

from textual.app import App
from textual.widgets import DataTable, Label

import hugin.trash as trash
from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
from hugin.engines import Engine
from hugin.scanner import Post
from hugin.tui.redirects_screen import ConfirmDeleteScreen
from hugin.tui.review import HuginScreen


def test_move_to_trash_keeps_content_and_avoids_collisions(tmp_path, monkeypatch):
    monkeypatch.setattr(trash, "TRASH_DIR", tmp_path / "trash")
    a = tmp_path / "post.md"
    a.write_text("one")
    first = trash.move_to_trash(a)
    a.write_text("two")
    second = trash.move_to_trash(a)
    assert not a.exists()
    assert first != second and first.read_text() == "one" and second.read_text() == "two"
    assert first.name.endswith("-post.md")


def test_trash_location_is_abbreviated_with_tilde():
    assert trash.trash_location().startswith("~/.hugin/")


def test_delete_modal_says_where_the_file_goes():
    async def go():
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(ConfirmDeleteScreen("Title", "/x/"))
            await pilot.pause()
            text = str(app.screen.query_one("#del-warning", Label).render())
            assert "moved to the trash folder" in text and trash.trash_location() in text

    asyncio.run(go())


def test_X_moves_post_to_trash(tmp_path, monkeypatch):
    monkeypatch.setattr(trash, "TRASH_DIR", tmp_path / "trash")
    posts_dir = tmp_path / "posts"
    posts_dir.mkdir()
    posts = []
    for i in range(2):
        p = posts_dir / f"p{i}.md"
        p.write_text(f"---\ntitle: P{i}\n---\nbody")
        posts.append(Post(p, {"title": f"P{i}"}, "body", False))
    site = MagicMock()
    site.post_url.return_value = "/x"
    site.warnings = []
    index = MagicMock()
    index.has_no_outgoing.return_value = False
    index.get_post_url.return_value = "/x/"
    index.get_link_keywords.return_value = ""
    index.find_similar.return_value = []

    class Host(App):
        def on_mount(self):
            self.push_screen(HuginScreen(
                posts=posts, all_posts=list(posts),
                engine=Engine("t", "http://localhost/v1", "m", 30, None),
                pool={}, state={}, directory=posts_dir,
                config=HuginConfig(LinksConfig(), EmbeddingsConfig(), FrontmatterConfig()),
                site=site, index=index,
            ))

    async def go():
        app = Host()
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.press("X")
            await pilot.pause()
            await pilot.click("#btn-delete")
            await pilot.pause()
            assert screen.query_one("#post-table", DataTable).row_count == 1

    asyncio.run(go())
    assert not (posts_dir / "p0.md").exists()
    assert [f.read_text() for f in (tmp_path / "trash").iterdir()] == ["---\ntitle: P0\n---\nbody"]
