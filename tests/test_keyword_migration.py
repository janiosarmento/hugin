"""Keywords are filled from link profiles once per blog, in a single local commit."""

import os
import subprocess
from pathlib import Path

import frontmatter
import pytest

import hugin.state as state_module
from hugin.migrations import KEYWORDS_FROM_PROFILES, fill_keywords_from_profiles, parse_profile
from hugin.scanner import load_posts

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
}


class FakeIndex:
    """Stands in for EmbeddingIndex: only the link profile lookup matters here."""

    def __init__(self, profiles: dict[str, str]):
        self.profiles = profiles
        self.loaded = False

    def _load_cache(self):
        self.loaded = True

    def get_link_keywords(self, post) -> str:
        return self.profiles.get(post.filename, "")


def _git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=directory, capture_output=True, text=True, check=True,
        env={**os.environ, **GIT_ENV},
    )
    return result.stdout


def _write(directory: Path, name: str, front: str) -> None:
    (directory / name).write_text(f"---\n{front}---\n\nCorpo.\n", encoding="utf-8")


@pytest.fixture
def blog(tmp_path, monkeypatch):
    monkeypatch.setattr(state_module, "STATE_DIR", tmp_path / "state")
    directory = tmp_path / "blog"
    directory.mkdir()
    _git(directory, "init", "-q")
    _write(directory, "with-keywords.md", 'title: "Com"\ndate: 2024-01-01\nkeywords:\n- ja\n')
    _write(directory, "needs-profile.md", 'title: "Sem"\ndate: 2024-01-02\n')
    _write(directory, "no-profile.md", 'title: "Nada"\ndate: 2024-01-03\n')
    _write(directory, "empty-list.md", 'title: "Vazio"\ndate: 2024-01-04\nkeywords: []\n')
    _git(directory, "add", "-A")
    _git(directory, "commit", "-q", "-m", "initial")
    return directory


def _posts(directory: Path):
    return load_posts(directory, [])


def _profiles():
    return {
        "needs-profile.md": "café, Hugo, café, python",
        "empty-list.md": "tags, blog",
        "with-keywords.md": "should not be used",
    }


def test_parse_profile_drops_blanks_and_repeats_case_insensitively():
    assert parse_profile(" Hugo , , hugo, Python ,") == ["Hugo", "Python"]
    assert parse_profile("") == []


def test_fills_posts_without_keywords_and_commits_only_them(blog):
    index = FakeIndex(_profiles())

    message = fill_keywords_from_profiles(blog, _posts(blog), index)

    assert index.loaded
    assert message.startswith("Keywords filled from link profiles for 1 post(s), committed (not pushed).")
    assert "1 post(s) have no keywords and no link profile" in message
    assert frontmatter.load(str(blog / "needs-profile.md")).metadata["keywords"] == ["café", "Hugo", "python"]
    assert frontmatter.load(str(blog / "with-keywords.md")).metadata["keywords"] == ["ja"]
    assert frontmatter.load(str(blog / "empty-list.md")).metadata["keywords"] == []
    assert frontmatter.load(str(blog / "no-profile.md")).metadata.get("keywords") is None

    subject = _git(blog, "log", "-1", "--format=%s")
    assert subject.strip() == "Fill keywords from link profiles for 1 post(s)"
    changed = _git(blog, "show", "--name-only", "--format=", "HEAD").split()
    assert changed == ["needs-profile.md"]
    assert _git(blog, "status", "--porcelain", "--untracked-files=no") == ""


def test_only_adds_the_keywords_block(blog):
    before = (blog / "needs-profile.md").read_text(encoding="utf-8")

    fill_keywords_from_profiles(blog, _posts(blog), FakeIndex(_profiles()))

    after = (blog / "needs-profile.md").read_text(encoding="utf-8")
    assert after == (
        '---\ntitle: "Sem"\ndate: 2024-01-02\nkeywords:\n- café\n- Hugo\n- python\n---\n\nCorpo.\n'
    )
    assert before.replace("---\n\nCorpo.", "keywords:\n- café\n- Hugo\n- python\n---\n\nCorpo.") == after


def test_runs_once_per_blog(blog):
    fill_keywords_from_profiles(blog, _posts(blog), FakeIndex(_profiles()))
    # Removed on purpose: the post keeps its profile, but must not be filled again
    _write(blog, "needs-profile.md", 'title: "Sem"\ndate: 2024-01-02\n')
    _git(blog, "commit", "-q", "-am", "drop keywords by hand")
    commits_before = _git(blog, "rev-list", "--count", "HEAD")

    assert fill_keywords_from_profiles(blog, _posts(blog), FakeIndex(_profiles())) is None
    assert _git(blog, "rev-list", "--count", "HEAD") == commits_before
    assert frontmatter.load(str(blog / "needs-profile.md")).metadata.get("keywords") is None


def test_does_nothing_with_tracked_changes(blog):
    (blog / "no-profile.md").write_text("edited by hand\n", encoding="utf-8")
    index = FakeIndex(_profiles())

    message = fill_keywords_from_profiles(blog, _posts(blog), index)

    assert "uncommitted changes" in message
    assert frontmatter.load(str(blog / "needs-profile.md")).metadata.get("keywords") is None
    assert _git(blog, "rev-list", "--count", "HEAD").strip() == "1"


def test_present_but_empty_keywords_are_left_alone(blog):
    fill_keywords_from_profiles(blog, _posts(blog), FakeIndex(_profiles()))

    text = (blog / "empty-list.md").read_text(encoding="utf-8")
    assert text.count("keywords:") == 1


def test_untracked_files_do_not_block(blog):
    (blog / "scratch.txt").write_text("not tracked\n", encoding="utf-8")

    message = fill_keywords_from_profiles(blog, _posts(blog), FakeIndex(_profiles()))

    assert message.startswith("Keywords filled")


def test_not_a_repository_fills_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(state_module, "STATE_DIR", tmp_path / "state")
    directory = tmp_path / "plain"
    directory.mkdir()
    _write(directory, "needs-profile.md", 'title: "Sem"\ndate: 2024-01-02\n')

    message = fill_keywords_from_profiles(directory, _posts(directory), FakeIndex(_profiles()))

    assert "not a Git repository" in message
    assert frontmatter.load(str(directory / "needs-profile.md")).metadata.get("keywords") is None


def test_no_candidates_leaves_no_marker(blog):
    index = FakeIndex({})

    assert fill_keywords_from_profiles(blog, _posts(blog), index) is None

    state = state_module.load_state(blog)
    assert KEYWORDS_FROM_PROFILES not in state.get("migrations", {})
