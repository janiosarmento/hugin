"""Shift+E opens the current post in $EDITOR and reloads it afterwards."""

from contextlib import contextmanager
from pathlib import Path

import pytest

from hugin.tui import review
from hugin.tui.review import STATE_BROWSING, STATE_LOADING, STATE_REVIEWING, HuginScreen


class FakeApp:
    @contextmanager
    def suspend(self):
        yield


class FakeScreen:
    action_external_editor = HuginScreen.action_external_editor

    def __init__(self, post_path: Path):
        self._state = STATE_BROWSING
        self.posts = [type("Post", (), {"path": post_path})()]
        self.current_index = 0
        self.app = FakeApp()
        self.notices: list[tuple[str, str]] = []
        self.reloaded: list[int] = []
        self.details_updated = 0

    def notify(self, message, *, severity="information", **_):
        self.notices.append((message, severity))

    def _reload_post(self, post, index):
        self.reloaded.append(index)

    def _update_detail_panel(self):
        self.details_updated += 1


def test_warns_when_editor_is_not_set(monkeypatch, tmp_path):
    monkeypatch.delenv("EDITOR", raising=False)
    ran = []
    monkeypatch.setattr(review.subprocess, "run", lambda *a, **k: ran.append(a))
    screen = FakeScreen(tmp_path / "post.md")

    screen.action_external_editor()

    assert ran == []
    assert screen.reloaded == []
    assert screen.notices and screen.notices[0][1] == "warning"
    assert "$EDITOR" in screen.notices[0][0]


def test_blank_editor_counts_as_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITOR", "   ")
    ran = []
    monkeypatch.setattr(review.subprocess, "run", lambda *a, **k: ran.append(a))
    screen = FakeScreen(tmp_path / "post.md")

    screen.action_external_editor()

    assert ran == []
    assert screen.notices[0][1] == "warning"


def test_runs_editor_with_arguments_then_reloads(monkeypatch, tmp_path):
    post_path = tmp_path / "post.md"
    monkeypatch.setenv("EDITOR", "code --wait")
    ran = []
    monkeypatch.setattr(review.subprocess, "run", lambda cmd, **k: ran.append(cmd))
    screen = FakeScreen(post_path)

    screen.action_external_editor()

    assert ran == [["code", "--wait", str(post_path)]]
    assert screen.reloaded == [0]
    assert screen.details_updated == 1
    assert screen.notices == []


@pytest.mark.parametrize("state", [STATE_LOADING, STATE_REVIEWING])
def test_ignored_outside_browsing(monkeypatch, tmp_path, state):
    monkeypatch.setenv("EDITOR", "hx")
    ran = []
    monkeypatch.setattr(review.subprocess, "run", lambda *a, **k: ran.append(a))
    screen = FakeScreen(tmp_path / "post.md")
    screen._state = state

    screen.action_external_editor()

    assert ran == []
    assert screen.reloaded == []
