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
    assert "[writing]" not in project.settings_path(blog).read_text()


def test_custom_constraints_and_per_language_voices_round_trip(monkeypatch, tmp_path):
    blog = _isolate(monkeypatch, tmp_path)
    config = ProjectConfig(writing=WritingSettings(
        constraints='Plain "quoted" rule.\nSecond line with a \\ backslash.',
        by_language={"Portuguese": "Write in Portuguese.", "English": "Write plainly."},
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


def test_author_profiles_round_trip(monkeypatch, tmp_path):
    blog = _isolate(monkeypatch, tmp_path)
    config = ProjectConfig()
    config.authors.profiles = {"Prof. Silva": "Formal, academic.", "Ana Lima": 'Warm and "informal".'}
    save_project(blog, config)

    assert load_project(blog).authors.profiles == config.authors.profiles


def test_author_list_order_round_trips(monkeypatch, tmp_path):
    blog = _isolate(monkeypatch, tmp_path)
    config = ProjectConfig()
    config.authors.names = ["Joe", "Ana"]
    config.authors.profiles = {"Ana": "Warm."}
    save_project(blog, config)

    loaded = load_project(blog).authors
    assert loaded.names == ["Joe", "Ana"]
    assert loaded.profiles == {"Ana": "Warm."}


def test_profiles_missing_from_the_list_are_added_to_it(monkeypatch, tmp_path):
    blog = _isolate(monkeypatch, tmp_path)
    (blog / "hugin").mkdir()
    (blog / "hugin" / "config.toml").write_text('[authors.profiles]\n"Ana" = "Warm."\n', encoding="utf-8")
    assert load_project(blog).authors.names == ["Ana"]


def test_no_author_profiles_are_not_written_to_disk(monkeypatch, tmp_path):
    blog = _isolate(monkeypatch, tmp_path)
    save_project(blog, ProjectConfig())
    assert "[authors" not in project.settings_path(blog).read_text()


def test_settings_are_saved_inside_the_blog(monkeypatch, tmp_path):
    blog = _isolate(monkeypatch, tmp_path)
    save_project(blog, ProjectConfig(writing=WritingSettings(constraints="custom")))
    assert (blog / "hugin" / "config.toml").exists()
    assert not any((tmp_path / "projects").glob("*.toml"))


def test_legacy_settings_are_read_until_the_blog_has_its_own(monkeypatch, tmp_path):
    blog = _isolate(monkeypatch, tmp_path)
    legacy = project.legacy_settings_path(blog)
    legacy.parent.mkdir(parents=True)
    legacy.write_text('[summary]\nwords = 33\n')
    assert load_project(blog).summary.words == 33

    save_project(blog, load_project(blog))
    legacy.write_text('[summary]\nwords = 99\n')  # stale copy must no longer win
    assert load_project(blog).summary.words == 33


def test_settings_are_shared_by_all_languages_at_the_site_root(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    site = tmp_path / "site"
    (site / "config" / "_default").mkdir(parents=True)
    (site / "config" / "_default" / "hugo.toml").write_text('baseURL = "https://x/"\n')
    en_posts = site / "content" / "en" / "post"
    pt_posts = site / "content" / "pt" / "post"
    en_posts.mkdir(parents=True)
    pt_posts.mkdir(parents=True)

    config = ProjectConfig()
    config.authors.names = ["Ana"]
    save_project(en_posts, config)

    assert (site / "hugin" / "config.toml").exists()
    assert not (en_posts / "hugin").exists()
    assert load_project(pt_posts).authors.names == ["Ana"]


def test_author_list_is_hugo_data_and_profiles_stay_in_settings(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    site = tmp_path / "site"
    (site / "config" / "_default").mkdir(parents=True)
    (site / "config" / "_default" / "hugo.toml").write_text('baseURL = "https://x/"\n')
    posts = site / "content" / "en" / "post"
    posts.mkdir(parents=True)
    config = ProjectConfig()
    config.authors.names = ["Joe", "Ana"]
    config.authors.profiles = {"Ana": "Warm."}
    save_project(posts, config)

    assert "names = [\"Joe\", \"Ana\"]" in (site / "data" / "authors.toml").read_text(encoding="utf-8")
    settings_text = (site / "hugin" / "config.toml").read_text(encoding="utf-8")
    assert "names" not in settings_text and '"Ana" = "Warm."' in settings_text


def test_author_list_in_old_settings_is_read_and_moves_on_save(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    site = tmp_path / "site"
    (site / "config" / "_default").mkdir(parents=True)
    (site / "config" / "_default" / "hugo.toml").write_text('baseURL = "https://x/"\n')
    (site / "hugin").mkdir()
    (site / "hugin" / "config.toml").write_text('[authors]\nnames = ["Bea"]\n', encoding="utf-8")

    assert load_project(site).authors.names == ["Bea"]
    save_project(site, load_project(site))
    assert (site / "data" / "authors.toml").exists()
    assert "names" not in (site / "hugin" / "config.toml").read_text(encoding="utf-8")
