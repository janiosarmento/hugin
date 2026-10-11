"""commit_settings: commits the Hugin settings file alone, then pulls and pushes."""

import subprocess
from pathlib import Path

import pytest

from hugin.git import SETTINGS_PUSHED, commit_settings


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout


@pytest.fixture
def blog_and_remote(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    blog = tmp_path / "blog"
    _git(tmp_path, "clone", "-q", str(remote), str(blog))
    (blog / "post.md").write_text("first\n")
    _git(blog, "add", "post.md")
    _git(blog, "commit", "-q", "-m", "post")
    _git(blog, "push", "-q", "-u", "origin", "main")
    return blog, remote


def test_settings_are_committed_and_pushed_without_posts(blog_and_remote):
    blog, remote = blog_and_remote
    (blog / "post.md").write_text("edited in progress\n")  # an unfinished post edit
    (blog / "hugin").mkdir()
    (blog / "hugin" / "config.toml").write_text("[summary]\nwords = 30\n")

    assert commit_settings(blog, "Hugin settings: update summary and links") == SETTINGS_PUSHED

    assert _git(blog, "log", "-1", "--format=%s") == "Hugin settings: update summary and links\n"
    assert _git(blog, "show", "--name-only", "--format=", "HEAD").split() == ["hugin/config.toml"]
    assert _git(blog, "status", "--porcelain").strip() == "M post.md"  # post edit left alone
    assert _git(remote, "log", "-1", "--format=%s", "main").startswith("Hugin settings")


def test_unchanged_settings_make_no_commit(blog_and_remote):
    blog, _ = blog_and_remote
    (blog / "hugin").mkdir()
    (blog / "hugin" / "config.toml").write_text("[summary]\nwords = 30\n")
    commit_settings(blog, "first")
    head = _git(blog, "rev-parse", "HEAD")

    assert commit_settings(blog, "again") is None
    assert _git(blog, "rev-parse", "HEAD") == head


def test_folder_that_is_not_a_repository_is_skipped(tmp_path):
    (tmp_path / "hugin").mkdir()
    (tmp_path / "hugin" / "config.toml").write_text("x = 1\n")
    assert commit_settings(tmp_path, "msg") is None


def test_author_list_and_settings_are_committed_together(blog_and_remote):
    blog, _ = blog_and_remote
    (blog / "hugin").mkdir()
    (blog / "hugin" / "config.toml").write_text("[summary]\nwords = 30\n")
    (blog / "data").mkdir()
    (blog / "data" / "authors.toml").write_text('names = ["Ana"]\n')

    assert commit_settings(blog, "Hugin settings: update author list") == SETTINGS_PUSHED
    assert sorted(_git(blog, "show", "--name-only", "--format=", "HEAD").split()) == [
        "data/authors.toml", "hugin/config.toml",
    ]
