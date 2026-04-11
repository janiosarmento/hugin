"""Fixtures compartilhadas para testes."""

import shutil
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture
def tmp_posts(tmp_path):
    """Cria diretório temporário com cópias dos fixtures."""
    for f in FIXTURES_DIR.glob("*.md"):
        shutil.copy(f, tmp_path / f.name)
    return tmp_path
