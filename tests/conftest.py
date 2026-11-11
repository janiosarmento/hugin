"""Shared fixtures for tests."""

import shutil
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture
def tmp_posts(tmp_path):
    """Creates a temporary directory with copies of the fixtures."""
    for f in FIXTURES_DIR.glob("*.md"):
        shutil.copy(f, tmp_path / f.name)
    return tmp_path


@pytest.fixture(autouse=True)
def isolated_log(tmp_path_factory, monkeypatch):
    """Tests never write to the real ~/.hugin/hugin.log."""
    import hugin.log

    monkeypatch.setattr(hugin.log, "LOG_PATH", tmp_path_factory.mktemp("log") / "hugin.log")


@pytest.fixture(autouse=True)
def no_search_key(monkeypatch):
    """Tests never read the real Brave Search key from the vault."""
    import hugin.research

    monkeypatch.setattr(hugin.research, "search_api_key", lambda: None)
