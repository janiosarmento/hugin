"""Tests for per-project settings (project.py)."""

import hugin.project as project
from hugin.project import (
    DEFAULT_EDITORIAL_CONSTRAINTS,
    ProjectConfig,
    WritingSettings,
    load_project,
    save_project,
)


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(project, "PROJECTS_DIR", tmp_path / "projects")
    blog = tmp_path / "blog"
    blog.mkdir()
    return blog


def test_missing_file_gives_default_constraints(monkeypatch, tmp_path):
    blog = _isolate(monkeypatch, tmp_path)
    assert load_project(blog).writing.constraints == DEFAULT_EDITORIAL_CONSTRAINTS


def test_default_constraints_are_not_written_to_disk(monkeypatch, tmp_path):
    blog = _isolate(monkeypatch, tmp_path)
    save_project(blog, ProjectConfig())
    assert "[writing]" not in project._project_path(blog).read_text()


def test_custom_constraints_and_per_language_voices_round_trip(monkeypatch, tmp_path):
    blog = _isolate(monkeypatch, tmp_path)
    config = ProjectConfig(writing=WritingSettings(
        constraints='Plain "quoted" rule.\nSecond line with a \\ backslash.',
        by_language={"Portuguese": "Escreva em português.", "English": "Write plainly."},
    ))
    save_project(blog, config)

    loaded = load_project(blog).writing
    assert loaded.constraints == config.writing.constraints
    assert loaded.by_language == config.writing.by_language


def test_for_language_falls_back_to_blog_constraints(monkeypatch, tmp_path):
    settings = WritingSettings(constraints="base", by_language={"Portuguese": "pt rules"})
    assert settings.for_language("Portuguese") == "pt rules"
    assert settings.for_language("Spanish") == "base"


def test_saving_other_settings_keeps_writing_settings(monkeypatch, tmp_path):
    blog = _isolate(monkeypatch, tmp_path)
    config = ProjectConfig(writing=WritingSettings(constraints="custom rules"))
    save_project(blog, config)
    reloaded = load_project(blog)
    reloaded.summary.words = 40
    save_project(blog, reloaded)
    assert load_project(blog).writing.constraints == "custom rules"
    assert load_project(blog).summary.words == 40
