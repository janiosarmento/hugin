import asyncio

from textual.app import App
from textual.binding import Binding

from hugin.tui.review import HelpScreen


class _Host(App):
    pass


def test_help_screen_survives_scroll_keys():
    async def run():
        app = _Host()
        async with app.run_test() as pilot:
            await app.push_screen(
                HelpScreen([Binding("t", "tags", "Tags", tooltip="Generate tags")])
            )
            await pilot.pause()
            await pilot.press("down", "up", "pagedown")
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, HelpScreen)

    asyncio.run(run())
