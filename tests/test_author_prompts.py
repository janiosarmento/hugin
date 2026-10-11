"""Author choice on the screens that create posts: n (new post) and news ideas."""

import asyncio

import frontmatter
from textual.app import App
from textual.widgets import Checkbox, Input, Select

from hugin.tui.news_ideas import NewsIdeasScreen
from hugin.tui.review import NewPostScreen


def _run_screen(screen, action):
    """Push `screen`, run `action(app, pilot)` on it, and return the dismiss result."""
    result = {}

    async def go():
        app = App()
        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(screen, lambda value: result.setdefault("value", value))
            await pilot.pause()
            await action(app, pilot)
            await pilot.pause()

    asyncio.run(go())
    return result.get("value")


def test_new_post_screen_returns_filename_and_chosen_author():
    async def action(app, pilot):
        app.screen.query_one("#newpost-input", Input).value = "gatos"
        app.screen.query_one("#newpost-author", Select).value = "Bia"
        app.screen.action_confirm()

    screen = NewPostScreen(["Ana", "Bia"], default_author="Ana")
    assert _run_screen(screen, action) == ("gatos.md", "Bia")


def test_new_post_screen_defaults_to_the_selected_posts_author():
    async def action(app, pilot):
        app.screen.query_one("#newpost-input", Input).value = "gatos"
        app.screen.action_confirm()

    assert _run_screen(NewPostScreen(["Ana", "Bia"], "Bia"), action) == ("gatos.md", "Bia")


def test_new_post_screen_without_authors_returns_no_author():
    async def action(app, pilot):
        app.screen.query_one("#newpost-input", Input).value = "gatos"
        app.screen.action_confirm()

    assert _run_screen(NewPostScreen(), action) == ("gatos.md", None)


def test_news_drafts_are_written_with_the_chosen_author(tmp_path):
    screen = NewsIdeasScreen(None, tmp_path, authors=["Ana", "Bia"], default_author="Ana")

    async def action(app, pilot):
        screen.query_one("#ideas-author", Select).value = "Bia"
        screen._idea_checkboxes = [
            (Checkbox("Gatos", value=True), {"title": "Gatos e história", "description": "d", "category": ""}),
        ]
        screen.action_create_drafts()

    _run_screen(screen, action)
    created = list(tmp_path.glob("*.md"))
    assert len(created) == 1
    assert frontmatter.load(str(created[0])).metadata["author"] == "Bia"
