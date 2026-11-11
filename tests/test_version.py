from click.testing import CliRunner

from hugin import version
from hugin.cli import main


def test_short_form():
    v = version.Version("0.1.0", 259, "0f7ec97", "2026-11-11")
    assert v.short() == "build 259 · 0f7ec97 · 2026-11-11"
    assert str(v) == "hugin 0.1.0, build 259 · 0f7ec97 · 2026-11-11"
    dirty = version.Version("0.1.0", 259, "0f7ec97", "2026-11-11", dirty=True)
    assert dirty.short().endswith(" · local changes")
    assert str(version.Version("0.1.0")) == "hugin 0.1.0 (build unknown)"


def test_read_from_this_checkout():
    v = version.current()
    assert v.build is not None and v.build > 0
    assert len(v.commit) >= 7
    assert v.date[:2] == "20"


def test_without_git_only_the_package_version(tmp_path, monkeypatch):
    monkeypatch.setattr(version, "REPO", tmp_path)
    version.current.cache_clear()
    try:
        assert version.current().build is None
    finally:
        version.current.cache_clear()


def test_cli_prints_the_version():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert result.output.startswith("hugin ") and "build " in result.output
