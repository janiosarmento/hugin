"""Pressing k copies the post's link profile into its keywords, without calling the LLM."""

from pathlib import Path

import frontmatter
import pytest

import hugin.state as state_module
from hugin.tui.review import STATE_BROWSING, HuginScreen


class FakeIndex:
    def __init__(self, profile: str):
        self.profile = profile

    def get_link_keywords(self, post) -> str:
        return self.profile


class FakeScreen:
    action_keywords = HuginScreen.action_keywords

    def __init__(self, post_path: Path, profile: str, directory: Path):
        self._state = STATE_BROWSING
        self.posts = [type("Post", (), {"path": post_path, "filename": post_path.name,
                                        "metadata": frontmatter.load(str(post_path)).metadata})()]
        self.current_index = 0
        self.index = FakeIndex(profile)
        self.state = {"posts": {}}
        self.directory = directory
        self.notices: list[tuple[str, str]] = []
        self.details_updated = 0

    def notify(self, message, *, severity="information", **_):
        self.notices.append((message, severity))

    def _update_detail_panel(self):
        self.details_updated += 1


@pytest.fixture
def post(tmp_path, monkeypatch):
    monkeypatch.setattr(state_module, "STATE_DIR", tmp_path / "state")
    path = tmp_path / "cats.md"
    path.write_text('---\ntitle: "Cats"\nkeywords:\n- velho\n---\n\nBody.\n', encoding="utf-8")
    return path


def test_replaces_keywords_with_the_link_profile(post, tmp_path):
    screen = FakeScreen(post, "café, Hugo, café, python", tmp_path)

    screen.action_keywords()

    assert frontmatter.load(str(post)).metadata["keywords"] == ["café", "Hugo", "python"]
    assert screen.posts[0].metadata["keywords"] == ["café", "Hugo", "python"]
    assert screen.notices[-1] == ("cats.md: 3 keywords from the link profile", "information")
    assert screen.details_updated == 1


def test_without_a_profile_warns_and_changes_nothing(post, tmp_path):
    before = post.read_text(encoding="utf-8")
    screen = FakeScreen(post, "", tmp_path)

    screen.action_keywords()

    assert post.read_text(encoding="utf-8") == before
    assert screen.notices[-1][1] == "warning"
    assert "Press o" in screen.notices[-1][0]


def test_ignored_while_not_browsing(post, tmp_path):
    before = post.read_text(encoding="utf-8")
    screen = FakeScreen(post, "café, python", tmp_path)
    screen._state = "loading"

    screen.action_keywords()

    assert post.read_text(encoding="utf-8") == before
