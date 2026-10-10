import asyncio

from textual.app import App
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Label

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


def test_underlying_screen_bindings_do_not_fire_while_help_is_open():
    fired = []

    class Main(Screen):
        BINDINGS = [Binding("n", "pick", "Engine")]

        def compose(self):
            yield Label("main")

        def action_pick(self):
            fired.append(True)

    class Host(App):
        def on_mount(self):
            self.push_screen(Main())

    async def run():
        app = Host()
        async with app.run_test() as pilot:
            await app.push_screen(HelpScreen(Main.BINDINGS))
            await pilot.pause()
            await pilot.press("n", "t", "k")
            await pilot.pause()
            assert fired == []
            assert isinstance(app.screen, HelpScreen)

    asyncio.run(run())


def test_display_key_keeps_case_and_marks_shift():
    from hugin.tui.review import _display_key

    assert _display_key("z") == "z"
    assert _display_key("Z") == "Shift+Z"
    assert _display_key("question_mark") == "?"
    assert _display_key("comma") == ","
