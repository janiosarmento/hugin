import json
import os
import stat

import pytest

import hugin.state as state
from hugin.fsutil import atomic_write_text, quarantine
from hugin.redirects import read_redirects, write_redirects


def test_atomic_write_creates_and_replaces(tmp_path):
    p = tmp_path / "sub" / "f.txt"
    atomic_write_text(p, "héllo")
    assert p.read_text(encoding="utf-8") == "héllo"
    atomic_write_text(p, "two")
    assert p.read_text() == "two"
    assert [x.name for x in p.parent.iterdir()] == ["f.txt"]  # no temp files left


def test_atomic_write_keeps_existing_permissions(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("a")
    os.chmod(p, 0o644)
    atomic_write_text(p, "b")
    assert stat.S_IMODE(p.stat().st_mode) == 0o644


def test_new_file_is_not_0600(tmp_path):
    p = tmp_path / "new.md"
    atomic_write_text(p, "a")
    assert stat.S_IMODE(p.stat().st_mode) & 0o044  # readable by group/others per umask


def test_failed_write_leaves_original_and_no_temp(tmp_path, monkeypatch):
    p = tmp_path / "f.txt"
    p.write_text("keep")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(p, "new")
    assert p.read_text() == "keep"
    assert [x.name for x in tmp_path.iterdir()] == ["f.txt"]


def test_quarantine_keeps_broken_file(tmp_path):
    p = tmp_path / "k.json"
    p.write_text("{broken")
    moved = quarantine(p)
    assert moved.name == "k.json.corrupt" and moved.read_text() == "{broken" and not p.exists()
    p.write_text("{again")
    assert quarantine(p).name == "k.json.corrupt-1"


def test_corrupt_state_does_not_break_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "state")
    state.save_state(tmp_path, {"directory": "x", "posts": {"a.md": {}}})
    path = state._state_path(tmp_path)
    path.write_text("{oops")
    assert state.load_state(tmp_path)["posts"] == {}
    assert list(path.parent.glob("*.corrupt"))


def test_redirects_round_trip_atomically(tmp_path):
    p = tmp_path / "static" / "_redirects"
    write_redirects(p, [("/a/", "/b/", "301")])
    assert read_redirects(p) == [("/a/", "/b/", "301")]


def test_save_post_preserves_permissions(tmp_path):
    import frontmatter

    from hugin.writer import save_post

    p = tmp_path / "a.md"
    p.write_text("---\ntitle: x\n---\nbody\n")
    os.chmod(p, 0o644)
    save_post(p, frontmatter.load(str(p)))
    assert stat.S_IMODE(p.stat().st_mode) == 0o644
    assert "body" in p.read_text()
