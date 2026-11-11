import asyncio
import importlib

import pytest
from textual.app import App

from hugin.tui.widgets import TextArea


def test_ctrl_a_selects_all_the_text():
    class Host(App):
        def compose(self):
            yield TextArea("one\ntwo\nthree")

    async def go():
        app = Host()
        async with app.run_test() as pilot:
            area = app.query_one(TextArea)
            area.focus()
            await pilot.pause()
            await pilot.press("ctrl+a")
            await pilot.pause()
            return area.selected_text

    assert asyncio.run(go()) == "one\ntwo\nthree"


@pytest.mark.parametrize("module", [
    "hugin.tui.editor", "hugin.tui.review", "hugin.tui.draft_screens",
    "hugin.tui.project_settings", "hugin.tui.redirects_screen",
])
def test_every_screen_uses_the_hugin_text_area(module):
    assert importlib.import_module(module).TextArea is TextArea
