"""Tests for hugin/config.py — config loading with fallback."""

import pytest
from pathlib import Path
from unittest.mock import patch

from hugin.config import load_config, LinksConfig, EmbeddingsConfig, FrontmatterConfig


class TestLoadConfig:
    def test_loads_from_links_toml(self, tmp_path):
        config_dir = tmp_path / ".hugin"
        config_dir.mkdir()
        (config_dir / "links.toml").write_text(
            "[links]\nmax_per_post = 5\n\n[embeddings]\n\n[frontmatter]\n"
        )
        with patch("hugin.config.CONFIG_DIR", config_dir):
            cfg = load_config()
        assert cfg.links.max_per_post == 5

    def test_fallback_to_munin_toml(self, tmp_path, capsys):
        config_dir = tmp_path / ".hugin"
        config_dir.mkdir()
        (config_dir / "munin.toml").write_text(
            "[links]\nmax_per_post = 12\n\n[embeddings]\n\n[frontmatter]\n"
        )
        with patch("hugin.config.CONFIG_DIR", config_dir):
            cfg = load_config()
            captured = capsys.readouterr()
        assert cfg.links.max_per_post == 12
        assert "munin.toml" in captured.err or "munin.toml" in captured.out

    def test_links_toml_takes_precedence(self, tmp_path):
        config_dir = tmp_path / ".hugin"
        config_dir.mkdir()
        (config_dir / "links.toml").write_text(
            "[links]\nmax_per_post = 3\n\n[embeddings]\n\n[frontmatter]\n"
        )
        (config_dir / "munin.toml").write_text(
            "[links]\nmax_per_post = 99\n\n[embeddings]\n\n[frontmatter]\n"
        )
        with patch("hugin.config.CONFIG_DIR", config_dir):
            cfg = load_config()
        assert cfg.links.max_per_post == 3

    def test_creates_default_when_missing(self, tmp_path):
        config_dir = tmp_path / ".hugin"
        with patch("hugin.config.CONFIG_DIR", config_dir):
            cfg = load_config()
        assert cfg.links.max_per_post == 8
        assert cfg.links.max_anchor_words == 5
        assert cfg.embeddings.model == "intfloat/multilingual-e5-large"
        assert (config_dir / "links.toml").exists()

    def test_default_values(self, tmp_path):
        config_dir = tmp_path / ".hugin"
        config_dir.mkdir()
        (config_dir / "links.toml").write_text(
            "[links]\n\n[embeddings]\n\n[frontmatter]\n"
        )
        with patch("hugin.config.CONFIG_DIR", config_dir):
            cfg = load_config()
        assert cfg.links.max_per_post == 8
        assert cfg.links.max_per_paragraph == 1
        assert cfg.links.words_per_link == 300
        assert cfg.links.candidates == 10
        assert cfg.links.max_anchor_words == 5
        assert cfg.frontmatter.summary_field == "description"
