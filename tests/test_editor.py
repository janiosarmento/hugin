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
