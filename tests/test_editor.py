"""Post editor: field widgets follow the field type (draft is a checkbox)."""

import asyncio

import frontmatter
from textual.app import App
from textual.widgets import Checkbox, Input

from hugin.scanner import load_posts
from hugin.tui.editor import EditorScreen


def _post(tmp_path, draft_line: str):
    text = f'---\ntitle: "Gatos"\nslug: gatos\ndate: 2024-01-02\n{draft_line}---\n\nCorpo.\n'
    (tmp_path / "gatos.md").write_text(text, encoding="utf-8")
    return load_posts(tmp_path, [])[0]


def _run(post, action):
    result = {}

    async def go():
        app = App()
        async with app.run_test(size=(120, 50)) as pilot:
            screen = EditorScreen(post)
            app.push_screen(screen)
            await pilot.pause()
            result["screen"] = screen
            result["dirty_before"] = screen._is_dirty()
            action(screen)
            await pilot.pause()
            result["dirty_after"] = screen._is_dirty()

    asyncio.run(go())
    return result


def test_draft_is_a_checkbox_that_reflects_the_post(tmp_path):
    result = _run(_post(tmp_path, "draft: true\n"), lambda s: None)
    widget = result["screen"]._field_inputs["draft"]
    assert isinstance(widget, Checkbox) and widget.value is True
    assert isinstance(result["screen"]._field_inputs["title"], Input)


def test_missing_draft_shows_unchecked(tmp_path):
    result = _run(_post(tmp_path, ""), lambda s: None)
    assert result["screen"]._field_inputs["draft"].value is False


def test_toggling_draft_marks_the_editor_dirty(tmp_path):
    def toggle(screen):
        screen._field_inputs["draft"].value = False

    result = _run(_post(tmp_path, "draft: true\n"), toggle)
    assert result["dirty_before"] is False
    assert result["dirty_after"] is True


def test_saving_writes_draft_as_a_boolean(tmp_path):
    post = _post(tmp_path, "draft: true\n")

    def uncheck_and_save(screen):
        screen._field_inputs["draft"].value = False
        screen.action_save()

    _run(post, uncheck_and_save)
    assert frontmatter.load(str(post.path)).metadata["draft"] is False


def test_saving_unchecked_does_not_add_draft_to_a_post_without_it(tmp_path):
    post = _post(tmp_path, "")

    def save(screen):
        screen.action_save()

    _run(post, save)
    assert "draft" not in frontmatter.load(str(post.path)).metadata


def test_toc_shares_the_draft_row_and_lastmod_is_not_shown(tmp_path):
    from textual.containers import Horizontal

    post = _post(tmp_path, "draft: true\ntoc: true\n")
    seen = {}

    async def go():
        app = App()
        async with app.run_test(size=(120, 50)) as pilot:
            screen = EditorScreen(post)
            app.push_screen(screen)
            await pilot.pause()
            seen["toc"] = screen._field_inputs["toc"]
            seen["row"] = screen._field_inputs["draft"].parent
            seen["toc_row"] = screen._field_inputs["toc"].parent
            seen["fields"] = set(screen._field_inputs)

    asyncio.run(go())
    assert isinstance(seen["toc"], Checkbox) and seen["toc"].value is True
    assert isinstance(seen["row"], Horizontal) and seen["toc_row"] is seen["row"]
    assert "lastmod" not in seen["fields"]


def test_saving_toc_as_a_boolean(tmp_path):
    post = _post(tmp_path, "toc: true\n")

    def uncheck_and_save(screen):
        screen._field_inputs["toc"].value = False
        screen.action_save()

    _run(post, uncheck_and_save)
    assert frontmatter.load(str(post.path)).metadata["toc"] is False


def test_invalid_date_blocks_the_save(tmp_path):
    post = _post(tmp_path, "")
    original = post.path.read_text(encoding="utf-8")

    def break_date_and_save(screen):
        screen._field_inputs["date"].value = "not a date"
        screen.action_save()

    _run(post, break_date_and_save)
    assert post.path.read_text(encoding="utf-8") == original


def test_picked_date_keeps_the_time_of_the_post(tmp_path):
    from datetime import datetime

    from hugin.tui.date_picker import DatePickerScreen, format_frontmatter_date

    result = {}

    async def go():
        app = App()
        async with app.run_test(size=(120, 50)) as pilot:
            app.push_screen(DatePickerScreen(datetime(2024, 1, 2, 11, 30)), lambda v: result.setdefault("v", v))
            await pilot.pause()
            await pilot.press("right", "enter")
            await pilot.pause()

    asyncio.run(go())
    assert result["v"] == datetime(2024, 1, 3, 11, 30)
    assert format_frontmatter_date(result["v"]) == "2024-01-03T11:30:00"


def test_picker_cancel_returns_none(tmp_path):
    from datetime import datetime

    from hugin.tui.date_picker import DatePickerScreen

    result = {}

    async def go():
        app = App()
        async with app.run_test(size=(120, 50)) as pilot:
            app.push_screen(DatePickerScreen(datetime(2024, 1, 2, 11, 30)), lambda v: result.setdefault("v", v))
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(go())
    assert result["v"] is None


def test_picker_opened_from_the_editor_shows_the_current_time_and_fits(tmp_path):
    from textual.widgets import Button, Input


    post = _post(tmp_path, "")
    post.path.write_text(post.path.read_text(encoding="utf-8").replace("date: 2024-01-02", "date: 2024-01-02T11:30:00"))
    post = load_posts(tmp_path, [])[0]
    seen = {}

    async def go():
        app = App()
        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(EditorScreen(post))
            await pilot.pause()
            app.screen.query_one("#pick-date", Button).press()
            await pilot.pause()
            await pilot.pause()
            picker = app.screen
            seen["picker"] = type(picker).__name__
            seen["time"] = picker.query_one("#picker-time-input", Input).value
            modal = picker.query_one("#picker-modal").region
            seen["inside"] = all(
                b.region.right <= modal.right and b.region.bottom <= modal.bottom
                for b in picker.query(Button)
            )

    asyncio.run(go())
    assert seen == {"picker": "DatePickerScreen", "time": "11:30", "inside": True}
