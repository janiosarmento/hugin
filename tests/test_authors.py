"""Default author for posts that have none (hugin/authors.py) and its startup step."""

from datetime import datetime

import frontmatter

from hugin.authors import (
    _with_author,
    add_author_to_posts,
    import_authors,
    ordered_authors,
)
from hugin.migrations import run_migrations
from hugin.project import AuthorsSettings
from hugin.scanner import Post, load_posts


POST = '---\ntitle: "Gatos"\nslug: gatos\ndate: 2024-01-02\ntags: []\n---\n\nCorpo do texto.\n'


def _post(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return Post(path, dict(frontmatter.loads(text).metadata), "", False, date=datetime(2024, 1, 2))


def test_author_goes_right_after_title_and_nothing_else_moves():
    out = _with_author(POST, "Ana Lima")
    assert out == POST.replace('title: "Gatos"\n', 'title: "Gatos"\nauthor: "Ana Lima"\n')


def test_empty_author_line_is_filled_in():
    text = '---\ntitle: X\nauthor:\n---\nBody\n'
    assert _with_author(text, "Ana") == '---\ntitle: X\nauthor: "Ana"\n---\nBody\n'


def test_crlf_files_keep_their_line_endings():
    text = '---\r\ntitle: X\r\n---\r\nBody\r\n'
    assert _with_author(text, "Ana") == '---\r\ntitle: X\r\nauthor: "Ana"\r\n---\r\nBody\r\n'


def test_file_without_front_matter_is_left_alone():
    assert _with_author("Just text\n", "Ana") is None


def test_order_is_the_configured_list_then_authors_found_only_on_posts(tmp_path):
    posts = [_post(tmp_path, "a.md", '---\ntitle: A\nauthor: "Bia"\n---\n')]
    settings = AuthorsSettings(names=["Zé", "Ana"])
    assert ordered_authors(settings, posts) == ["Zé", "Ana", "Bia"]
    assert ordered_authors(settings, posts)[0] == "Zé"
    assert ordered_authors(AuthorsSettings(), posts)[0] == "Bia"
    assert ordered_authors(AuthorsSettings(), []) == []


def test_import_adds_post_authors_to_the_end_once(tmp_path):
    posts = [
        _post(tmp_path, "a.md", '---\ntitle: A\nauthor: "Bia"\n---\n'),
        _post(tmp_path, "b.md", '---\ntitle: B\nauthor: "Ana"\n---\n'),
    ]
    settings = AuthorsSettings(names=["Ana"])
    assert import_authors(settings, posts) == ["Bia"]
    assert settings.names == ["Ana", "Bia"]
    assert import_authors(settings, posts) == []


def test_add_author_only_touches_posts_without_one(tmp_path):
    missing = _post(tmp_path, "gatos.md", POST)
    has = _post(tmp_path, "ok.md", '---\ntitle: Ok\nauthor: "Bia"\n---\nBody\n')
    changed = add_author_to_posts([missing, has], "Ana Lima")
    assert changed == [missing.path]
    assert frontmatter.load(str(missing.path)).metadata["author"] == "Ana Lima"
    assert has.path.read_text(encoding="utf-8").count("author:") == 1
    assert frontmatter.load(str(missing.path)).content.strip() == "Corpo do texto."


def test_startup_imports_authors_from_posts_and_defaults_to_the_first(tmp_path):
    (tmp_path / "gatos.md").write_text(POST, encoding="utf-8")  # no author
    (tmp_path / "bia.md").write_text('---\ntitle: B\nauthor: "Bia"\n---\nBody\n', encoding="utf-8")
    posts = load_posts(tmp_path, [])

    assert run_migrations(tmp_path, posts)
    migrated = load_posts(tmp_path, [])
    assert migrated[0].metadata["author"] == "Bia"  # the only author on the posts
    from hugin.project import load_project
    assert load_project(tmp_path).authors.names == ["Bia"]  # written to the blog
    assert run_migrations(tmp_path, migrated) == []


def test_startup_step_sets_default_from_the_config_list(tmp_path):
    (tmp_path / "hugin").mkdir()
    (tmp_path / "hugin" / "config.toml").write_text(
        '[authors.profiles]\n"Ana Lima" = "Warm."\n"Prof. Silva" = "Formal."\n', encoding="utf-8"
    )
    (tmp_path / "gatos.md").write_text(POST, encoding="utf-8")
    posts = load_posts(tmp_path, [])

    assert run_migrations(tmp_path, posts)
    migrated = load_posts(tmp_path, [])
    assert migrated[0].metadata["author"] == "Ana Lima"
    # Second startup: nothing left to do
    assert run_migrations(tmp_path, migrated) == []


def test_startup_step_without_any_author_list_changes_nothing(tmp_path):
    (tmp_path / "gatos.md").write_text(POST, encoding="utf-8")
    posts = load_posts(tmp_path, [])
    assert run_migrations(tmp_path, posts) == []
    assert (tmp_path / "gatos.md").read_text(encoding="utf-8") == POST


def test_authors_list_form_is_read_like_the_author_field(tmp_path):
    from hugin.authors import authors_of, post_author, post_authors

    listed = _post(tmp_path, "a.md", "---\ntitle: A\nauthors:\n- mariana\n---\n")
    both = _post(tmp_path, "b.md", '---\ntitle: B\nauthor: "fabior"\nauthors: ["felipe", "fabior"]\n---\n')
    assert post_author(listed) == "mariana"
    assert post_authors(both) == ["fabior", "felipe"]
    assert authors_of([listed, both]) == ["mariana", "fabior", "felipe"]


def test_default_author_never_writes_next_to_an_authors_list(tmp_path):
    listed = _post(tmp_path, "a.md", "---\ntitle: A\nauthors:\n- mariana\n---\nBody\n")
    assert add_author_to_posts([listed], "Ana") == []
    assert "author:" not in listed.path.read_text(encoding="utf-8")


def test_default_author_writes_the_blogs_field_form(tmp_path):
    post = _post(tmp_path, "a.md", POST)
    add_author_to_posts([post], "mariana", "authors")
    assert frontmatter.load(str(post.path)).metadata["authors"] == ["mariana"]
    assert "author:" not in post.path.read_text(encoding="utf-8")


def test_new_post_uses_the_configured_field(tmp_path):
    from hugin.writer import create_post

    create_post(tmp_path / "n.md", title="N", slug="n", author="mariana", author_field="authors")
    assert frontmatter.load(str(tmp_path / "n.md")).metadata["authors"] == ["mariana"]
    create_post(tmp_path / "s.md", title="S", slug="s", author="Ana")
    assert frontmatter.load(str(tmp_path / "s.md")).metadata["author"] == "Ana"


def test_author_field_setting_round_trips(monkeypatch, tmp_path):
    import hugin.project as project
    from hugin.project import ProjectConfig, load_project, save_project

    monkeypatch.setattr(project, "PROJECTS_DIR", tmp_path / "projects")
    site = tmp_path / "site"
    (site / "config" / "_default").mkdir(parents=True)
    (site / "config" / "_default" / "hugo.toml").write_text('baseURL = "https://x/"\n')
    posts = site / "content" / "posts"
    posts.mkdir(parents=True)
    config = ProjectConfig()
    config.authors.author_field = "authors"
    save_project(posts, config)
    assert load_project(posts).authors.author_field == "authors"
    assert load_project(posts).authors.names == []


def test_pick_author_keeps_the_preferred_one_or_takes_the_first():
    from hugin.authors import pick_author

    assert pick_author("Bia", ["Ana", "Bia"]) == "Bia"
    assert pick_author("Zé", ["Ana", "Bia"]) == "Ana"
    assert pick_author(None, []) is None


def test_startup_warns_only_when_posts_lack_authors_and_no_list_exists(tmp_path):
    from hugin.cli import _missing_author_warning

    (tmp_path / "a.md").write_text("---\ntitle: A\n---\nBody\n", encoding="utf-8")
    posts = load_posts(tmp_path, [])
    assert _missing_author_warning(tmp_path, posts).startswith("Warning: 1 post(s)")

    (tmp_path / "hugin").mkdir()
    (tmp_path / "hugin" / "config.toml").write_text('[authors]\nnames = ["Ana"]\n', encoding="utf-8")
    assert _missing_author_warning(tmp_path, posts) is None


def test_author_goes_after_a_title_that_spans_lines():
    text = "---\ntitle: 'Resenha: Antes de Partir: Os 5 principais\n  arrependimentos'\nslug: x\n---\nBody\n"
    out = _with_author(text, "Janio Sarmento")
    assert out == (
        "---\ntitle: 'Resenha: Antes de Partir: Os 5 principais\n  arrependimentos'\n"
        'author: "Janio Sarmento"\nslug: x\n---\nBody\n'
    )
