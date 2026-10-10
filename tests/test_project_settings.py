"""Tests for the project settings screen's editorial rules."""

import asyncio

from textual.app import App
from textual.widgets import Input, TextArea

import hugin.project as project
from hugin.project import DEFAULT_EDITORIAL_CONSTRAINTS, ProjectConfig, WritingSettings, load_project, save_project
from hugin.tui.project_settings import ProjectSettingsScreen


def _setup(monkeypatch, tmp_path, config=None):
    monkeypatch.setattr(project, "PROJECTS_DIR", tmp_path / "projects")
    blog = tmp_path / "blog"
    blog.mkdir()
    config = config or ProjectConfig()
    save_project(blog, config)
    return blog, config


def _save(blog, config, edit):
    """Open the screen, apply `edit(screen)`, and press Save."""
    async def go():
        app = App()
        async with app.run_test(size=(100, 60)) as pilot:
            screen = ProjectSettingsScreen(config, blog)
            app.push_screen(screen)
            await pilot.pause()
            edit(screen)
            screen._do_save()
            await pilot.pause()
    asyncio.run(go())


def test_default_rules_are_short_and_do_not_ban_anything():
    assert "Avoid" not in DEFAULT_EDITORIAL_CONSTRAINTS
    assert len(DEFAULT_EDITORIAL_CONSTRAINTS.split()) < 60


def test_saving_blog_rules_and_a_language_override(monkeypatch, tmp_path):
    blog, config = _setup(monkeypatch, tmp_path)

    def edit(screen):
        screen.query_one("#input-constraints", TextArea).load_text("Blog rule.")
        screen.query_one("#input-override-language", Input).value = "Portuguese"
        screen.query_one("#input-override-rules", TextArea).load_text("Regra em português.")

    _save(blog, config, edit)
    loaded = load_project(blog).writing
    assert loaded.constraints == "Blog rule."
    assert loaded.by_language == {"Portuguese": "Regra em português."}


def test_renaming_an_override_moves_it(monkeypatch, tmp_path):
    config = ProjectConfig(writing=WritingSettings(by_language={"Portuguese": "pt"}))
    blog, config = _setup(monkeypatch, tmp_path, config)

    def edit(screen):
        screen.query_one("#input-override-language", Input).value = "Spanish"

    _save(blog, config, edit)
    assert load_project(blog).writing.by_language == {"Spanish": "pt"}


def test_blank_override_rules_remove_the_override(monkeypatch, tmp_path):
    config = ProjectConfig(writing=WritingSettings(by_language={"English": "en rules"}))
    blog, config = _setup(monkeypatch, tmp_path, config)

    def edit(screen):
        screen.query_one("#input-override-rules", TextArea).load_text("")

    _save(blog, config, edit)
    assert load_project(blog).writing.by_language == {}


def test_blank_blog_rules_restore_the_default(monkeypatch, tmp_path):
    config = ProjectConfig(writing=WritingSettings(constraints="custom"))
    blog, config = _setup(monkeypatch, tmp_path, config)

    def edit(screen):
        screen.query_one("#input-constraints", TextArea).load_text("   ")

    _save(blog, config, edit)
    assert load_project(blog).writing.constraints == DEFAULT_EDITORIAL_CONSTRAINTS


def test_rules_without_a_language_name_are_not_saved(monkeypatch, tmp_path):
    blog, config = _setup(monkeypatch, tmp_path)

    def edit(screen):
        screen.query_one("#input-override-rules", TextArea).load_text("orphan rules")

    _save(blog, config, edit)
    assert load_project(blog).writing.by_language == {}
