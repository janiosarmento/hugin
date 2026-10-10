import subprocess

import pytest

import hugin.git as hgit


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"],
                        "HOME": str(cwd)})


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A clone with an upstream, identity configured through the environment."""
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(work)], check=True, capture_output=True)
    (work / "a.md").write_text("one")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "push", "-u", "origin", "HEAD:main")
    return work


def test_happy_path_commits_and_pushes(repo):
    (repo / "new.md").write_text("hello")
    result = hgit.git_sync(repo)
    assert result.success, result.output
    ok, out = hgit._run(repo, ["git", "status", "--porcelain"])
    assert out == ""


def test_refuses_new_secret_files(repo):
    (repo / ".env").write_text("TOKEN=abc")
    (repo / "post.md").write_text("x")
    result = hgit.git_sync(repo)
    assert not result.success and ".env" in result.output and "secrets" in result.output
    ok, out = hgit._run(repo, ["git", "log", "--oneline"])
    assert len(out.splitlines()) == 1  # nothing was committed


def test_secret_detection_ignores_modified_tracked_files():
    assert hgit.secret_files(["?? keys/server.pem", "A  .env.local", " M notes.md", "?? post.md"]) == [
        "keys/server.pem", ".env.local",
    ]
    assert hgit.secret_files([" M .env"]) == []


def test_refuses_when_a_rebase_is_already_in_progress(repo):
    (repo / ".git" / "rebase-merge").mkdir()
    result = hgit.git_sync(repo)
    assert not result.success and "rebase is already in progress" in result.output
    assert (repo / ".git" / "rebase-merge").exists()  # left untouched


def test_conflicting_pull_is_aborted_and_reported(repo, tmp_path):
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(tmp_path / "remote.git"), str(other)], check=True, capture_output=True)
    (other / "a.md").write_text("remote change")
    _git(other, "commit", "-am", "remote")
    _git(other, "push")
    (repo / "a.md").write_text("local change")
    result = hgit.git_sync(repo)
    assert not result.success
    assert hgit._operation_in_progress(repo) is None  # our own rebase was aborted
    assert (repo / "a.md").read_text() == "local change"  # our commit is intact


def test_timeout_and_missing_git_are_reported_not_raised(tmp_path, monkeypatch):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(hgit.subprocess, "run", slow)
    ok, out = hgit._run(tmp_path, ["git", "pull"], timeout=1)
    assert not ok and "timed out" in out

    def missing(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(hgit.subprocess, "run", missing)
    ok, out = hgit._run(tmp_path, ["git", "status"])
    assert not ok and "not installed" in out


def test_git_never_waits_for_credentials(tmp_path, monkeypatch):
    seen = {}

    def fake(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(hgit.subprocess, "run", fake)
    hgit._run(tmp_path, ["git", "status"])
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert seen["stdin"] == subprocess.DEVNULL and seen["timeout"] == hgit.LOCAL_TIMEOUT


def test_confirm_modal_lists_pending_changes():
    import asyncio

    from textual.app import App
    from textual.widgets import Static

    from hugin.tui.review import ConfirmGitSyncScreen

    changes = [f" M post-{i}.md" for i in range(15)]

    async def go():
        app = App()
        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(ConfirmGitSyncScreen(changes))
            await pilot.pause()
            text = str(app.screen.query_one("#gitsync-changes", Static).render())
            assert "post-0.md" in text and "post-11.md" in text
            assert "post-12.md" not in text and "and 3 more" in text

    asyncio.run(go())
