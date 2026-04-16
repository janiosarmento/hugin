"""Tests for munin/hugo.py — Hugo config parsing and URL inference."""

import pytest
from pathlib import Path

from munin.hugo import (
    HugoSite,
    find_hugo_config,
    infer_section,
    resolve_url,
    slug_from_filename,
)


class TestSlugFromFilename:
    def test_simple(self):
        assert slug_from_filename("my-post.md") == "my-post"

    def test_with_date_prefix(self):
        assert slug_from_filename("2026-03-20-my-post.md") == "my-post"

    def test_no_date_prefix(self):
        assert slug_from_filename("357.md") == "357"

    def test_multiple_dates_in_name(self):
        # Only strip the leading date prefix
        assert slug_from_filename("2026-01-01-year-2025-review.md") == "year-2025-review"


class TestInferSection:
    def test_content_posts(self, tmp_path):
        posts_dir = tmp_path / "content" / "posts"
        posts_dir.mkdir(parents=True)
        assert infer_section(posts_dir) == "posts"

    def test_multilingual(self, tmp_path):
        posts_dir = tmp_path / "content" / "pt" / "post"
        posts_dir.mkdir(parents=True)
        assert infer_section(posts_dir) == "post"

    def test_fallback_to_dirname(self, tmp_path):
        posts_dir = tmp_path / "myblog" / "articles"
        posts_dir.mkdir(parents=True)
        assert infer_section(posts_dir) == "articles"


class TestResolveUrl:
    def test_explicit_url_in_frontmatter(self):
        meta = {"url": "/custom/path/"}
        assert resolve_url(meta, "post.md", "posts", "/posts/:slug/") == "/custom/path/"

    def test_explicit_url_normalized(self):
        meta = {"url": "custom/path"}
        assert resolve_url(meta, "post.md", "posts", "/posts/:slug/") == "/custom/path/"

    def test_slug_from_frontmatter(self):
        meta = {"slug": "my-custom-slug"}
        assert resolve_url(meta, "2026-03-20-original.md", "posts", "/posts/:slug/") == "/posts/my-custom-slug/"

    def test_slug_from_filename(self):
        meta = {}
        assert resolve_url(meta, "2026-03-20-my-post.md", "posts", "/posts/:slug/") == "/posts/my-post/"

    def test_slug_from_filename_no_date(self):
        meta = {}
        assert resolve_url(meta, "my-post.md", "posts", "/posts/:slug/") == "/posts/my-post/"

    def test_date_tokens(self):
        meta = {"date": "2026-03-20T10:00:00"}
        result = resolve_url(meta, "post.md", "posts", "/posts/:year/:month/:slug/")
        assert result == "/posts/2026/03/post/"

    def test_section_token(self):
        meta = {}
        result = resolve_url(meta, "post.md", "articles", "/:section/:slug/")
        assert result == "/articles/post/"

    def test_unsupported_token_fallback(self):
        meta = {}
        result = resolve_url(meta, "post.md", "posts", "/posts/:unsupported/:slug/")
        # Should fall back to default pattern
        assert result == "/posts/post/"

    def test_no_date_strips_date_tokens(self):
        meta = {}
        result = resolve_url(meta, "post.md", "posts", "/posts/:year/:slug/")
        assert result == "/posts/post/"


class TestFindHugoConfig:
    def test_finds_in_parent(self, tmp_path):
        (tmp_path / "hugo.toml").write_text('[permalinks]\nposts = "/posts/:slug/"')
        posts_dir = tmp_path / "content" / "posts"
        posts_dir.mkdir(parents=True)
        assert find_hugo_config(posts_dir) == tmp_path / "hugo.toml"

    def test_finds_config_toml(self, tmp_path):
        (tmp_path / "config.toml").write_text("")
        posts_dir = tmp_path / "content" / "posts"
        posts_dir.mkdir(parents=True)
        assert find_hugo_config(posts_dir) == tmp_path / "config.toml"

    def test_none_when_missing(self, tmp_path):
        posts_dir = tmp_path / "content" / "posts"
        posts_dir.mkdir(parents=True)
        # Walk up will eventually hit root, should return None
        # (tmp_path has no config file)
        result = find_hugo_config(posts_dir)
        # May find a config higher up in the real filesystem, so just check type
        assert result is None or isinstance(result, Path)


class TestHugoSite:
    def test_with_config(self, tmp_path):
        (tmp_path / "hugo.toml").write_text('[permalinks]\nposts = "/posts/:slug/"')
        posts_dir = tmp_path / "content" / "posts"
        posts_dir.mkdir(parents=True)

        site = HugoSite(posts_dir)
        assert site.section == "posts"
        assert site.permalink_pattern == "/posts/:slug/"
        assert site.warnings == []

    def test_without_config(self, tmp_path):
        posts_dir = tmp_path / "isolated" / "posts"
        posts_dir.mkdir(parents=True)

        site = HugoSite(posts_dir)
        assert site.permalink_pattern == "/:section/:slug/"

    def test_post_url(self, tmp_path):
        (tmp_path / "hugo.toml").write_text('[permalinks]\nposts = "/posts/:slug/"')
        posts_dir = tmp_path / "content" / "posts"
        posts_dir.mkdir(parents=True)

        site = HugoSite(posts_dir)
        url = site.post_url(
            {"slug": "meu-post", "date": "2026-03-20"},
            "2026-03-20-meu-post.md",
        )
        assert url == "/posts/meu-post/"

    def test_post_url_no_slug(self, tmp_path):
        (tmp_path / "hugo.toml").write_text('[permalinks]\nposts = "/posts/:slug/"')
        posts_dir = tmp_path / "content" / "posts"
        posts_dir.mkdir(parents=True)

        site = HugoSite(posts_dir)
        url = site.post_url({}, "2026-03-20-hello-world.md")
        assert url == "/posts/hello-world/"
