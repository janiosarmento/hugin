"""Startup migrations: each one independent, each one idempotent, any blog."""

import tomllib
from pathlib import Path

import frontmatter

from hugin.migrations import (
    add_post_authors_to_list,
    copy_legacy_settings,
    default_author_for_posts,
    move_author_list_to_hugo_data,
    run_migrations,
)
from hugin.authors import AuthorsSettings
from hugin.project import (
    ProjectConfig,
    authors_path,
    legacy_settings_path,
    load_project,
    save_project,
    settings_path,
)
from hugin.scanner import load_posts

POST = '---\ntitle: "Gatos"\nslug: gatos\ndate: 2024-01-02\n---\n\nCorpo.\n'
BY_BIA = '---\ntitle: "Bia"\nauthor: "Bia"\ndate: 2024-01-03\n---\n\nOutro.\n'


def _site(monkeypatch, tmp_path, posts=None):
    """A Hugo site with an English posts folder; returns (site root, posts folder)."""
    import hugin.project as project

    monkeypatch.setattr(project, "PROJECTS_DIR", tmp_path / "home" / "projects")
    site = tmp_path / "site"
    (site / "config" / "_default").mkdir(parents=True)
    (site / "config" / "_default" / "hugo.toml").write_text('baseURL = "https://x/"\n')
    posts_dir = site / "content" / "en" / "post"
    posts_dir.mkdir(parents=True)
    for name, text in (posts or {}).items():
        (posts_dir / name).write_text(text, encoding="utf-8")
    return site, posts_dir


def _names(site: Path) -> list[str]:
    return tomllib.loads((site / "data" / "authors.toml").read_text(encoding="utf-8")).get("names", [])


def _config_text(site: Path) -> str:
    return (site / "hugin" / "config.toml").read_text(encoding="utf-8")


# --- each migration on its own

def test_copy_legacy_settings_runs_once(monkeypatch, tmp_path):
    site, posts_dir = _site(monkeypatch, tmp_path)
    legacy = legacy_settings_path(posts_dir)
    legacy.parent.mkdir(parents=True)
    legacy.write_text('[summary]\nwords = 33\n', encoding="utf-8")

    assert copy_legacy_settings(posts_dir, []).changed is True
    assert load_project(posts_dir).summary.words == 33
    assert copy_legacy_settings(posts_dir, []).changed is False


def test_move_author_list_alone_on_a_config_with_names(monkeypatch, tmp_path):
    site, posts_dir = _site(monkeypatch, tmp_path)
    (site / "hugin").mkdir()
    (site / "hugin" / "config.toml").write_text('[authors]\nnames = ["Ana", "Bia"]\n', encoding="utf-8")

    assert move_author_list_to_hugo_data(posts_dir, []).changed is True
    assert _names(site) == ["Ana", "Bia"]
    assert "names" not in _config_text(site)
    assert move_author_list_to_hugo_data(posts_dir, []).changed is False


def test_add_post_authors_alone_appends_only_new_names(monkeypatch, tmp_path):
    site, posts_dir = _site(monkeypatch, tmp_path, {"bia.md": BY_BIA})
    save_project(posts_dir, ProjectConfig(authors=AuthorsSettings(names=["Ana"])))
    posts = load_posts(posts_dir, [])

    assert add_post_authors_to_list(posts_dir, posts).changed is True
    assert _names(site) == ["Ana", "Bia"]
    assert add_post_authors_to_list(posts_dir, posts).changed is False


def test_default_author_alone_only_touches_posts_without_one(monkeypatch, tmp_path):
    site, posts_dir = _site(monkeypatch, tmp_path, {"gatos.md": POST, "bia.md": BY_BIA})
    save_project(posts_dir, ProjectConfig(authors=AuthorsSettings(names=["Ana", "Bia"])))
    posts = load_posts(posts_dir, [])

    assert default_author_for_posts(posts_dir, posts).changed is True
    assert frontmatter.load(str(posts_dir / "gatos.md")).metadata["author"] == "Ana"
    assert frontmatter.load(str(posts_dir / "bia.md")).metadata["author"] == "Bia"
    assert default_author_for_posts(posts_dir, load_posts(posts_dir, [])).changed is False


# --- the whole sequence

def test_full_run_on_an_old_blog_then_nothing_on_the_second_run(monkeypatch, tmp_path):
    legacy_site, posts_dir = _site(monkeypatch, tmp_path, {"gatos.md": POST, "bia.md": BY_BIA})
    legacy = legacy_settings_path(posts_dir)
    legacy.parent.mkdir(parents=True)
    legacy.write_text('[authors]\nnames = ["Ana"]\n\n[authors.profiles]\n"Ana" = "Warm."\n', encoding="utf-8")

    first = run_migrations(posts_dir, load_posts(posts_dir, []))
    assert first.changed is True
    assert _names(legacy_site) == ["Ana", "Bia"]  # the list, then the author found on the posts
    assert frontmatter.load(str(posts_dir / "gatos.md")).metadata["author"] == "Ana"
    assert load_project(posts_dir).authors.profiles == {"Ana": "Warm."}

    snapshot = {p: p.read_bytes() for p in legacy_site.rglob("*") if p.is_file() and ".git" not in p.parts}
    second = run_migrations(posts_dir, load_posts(posts_dir, []))
    assert second.changed is False and second.messages == []
    assert {p: p.read_bytes() for p in snapshot} == snapshot  # not a byte changed


def test_a_blog_that_is_already_migrated_is_left_alone(monkeypatch, tmp_path):
    site, posts_dir = _site(monkeypatch, tmp_path, {"bia.md": BY_BIA})
    save_project(posts_dir, ProjectConfig(authors=AuthorsSettings(names=["Bia"])))
    before = settings_path(posts_dir).read_bytes(), authors_path(posts_dir).read_bytes()

    result = run_migrations(posts_dir, load_posts(posts_dir, []))
    assert result.changed is False
    assert (settings_path(posts_dir).read_bytes(), authors_path(posts_dir).read_bytes()) == before


def test_no_author_list_anywhere_warns_and_changes_nothing(monkeypatch, tmp_path):
    site, posts_dir = _site(monkeypatch, tmp_path, {"gatos.md": POST})
    result = run_migrations(posts_dir, load_posts(posts_dir, []))
    assert result.changed is False
    assert any(m.startswith("Warning:") for m in result.messages)
    assert (posts_dir / "gatos.md").read_text(encoding="utf-8") == POST


def test_each_blog_is_migrated_independently(monkeypatch, tmp_path):
    """Two blogs with different histories: running one never touches the other."""
    a_site, a_posts = _site(monkeypatch, tmp_path / "a", {"x.md": POST})
    b_site, b_posts = _site(monkeypatch, tmp_path / "b", {"y.md": BY_BIA})
    run_migrations(b_posts, load_posts(b_posts, []))
    assert not (a_site / "data").exists()
    assert not (a_site / "hugin").exists()
    assert (a_posts / "x.md").read_text(encoding="utf-8") == POST
