"""Default author for posts that have none (hugin/authors.py) and its startup step."""

from datetime import datetime

import frontmatter

from hugin.authors import (
    _with_author,
    add_author_to_posts,
    default_author,
    import_authors,
    ordered_authors,
)
from hugin.cli import _normalize_authors
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
    assert default_author(settings, posts) == "Zé"
    assert default_author(AuthorsSettings(), posts) == "Bia"
    assert default_author(AuthorsSettings(), []) is None


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

    assert _normalize_authors(tmp_path, posts) is True
    migrated = load_posts(tmp_path, [])
    assert migrated[0].metadata["author"] == "Bia"  # the only author on the posts
    from hugin.project import load_project
    assert load_project(tmp_path).authors.names == ["Bia"]  # written to the blog
    assert _normalize_authors(tmp_path, migrated) is False


def test_startup_step_sets_default_from_the_config_list(tmp_path):
    (tmp_path / "hugin").mkdir()
    (tmp_path / "hugin" / "config.toml").write_text(
        '[authors.profiles]\n"Ana Lima" = "Warm."\n"Prof. Silva" = "Formal."\n', encoding="utf-8"
    )
    (tmp_path / "gatos.md").write_text(POST, encoding="utf-8")
    posts = load_posts(tmp_path, [])

    assert _normalize_authors(tmp_path, posts) is True
    migrated = load_posts(tmp_path, [])
    assert migrated[0].metadata["author"] == "Ana Lima"
    # Second startup: nothing left to do
    assert _normalize_authors(tmp_path, migrated) is False


def test_startup_step_without_any_author_list_changes_nothing(tmp_path):
    (tmp_path / "gatos.md").write_text(POST, encoding="utf-8")
    posts = load_posts(tmp_path, [])
    assert _normalize_authors(tmp_path, posts) is False
    assert (tmp_path / "gatos.md").read_text(encoding="utf-8") == POST
