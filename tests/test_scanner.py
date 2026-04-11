"""Testes do módulo scanner."""

from datetime import datetime

from hugin.scanner import (
    collect_tag_pool,
    find_duplicate_tags,
    format_pool_for_prompt,
    load_posts,
    prioritize,
)


class TestLoadPosts:
    def test_loads_yaml_posts(self, tmp_posts):
        posts = load_posts(tmp_posts)
        filenames = {p.filename for p in posts}
        assert "post-with-tags.md" in filenames
        assert "post-no-tags.md" in filenames
        assert "post-draft.md" in filenames

    def test_ignores_toml_frontmatter(self, tmp_posts):
        posts = load_posts(tmp_posts)
        filenames = {p.filename for p in posts}
        assert "post-toml-frontmatter.md" not in filenames

    def test_parses_tags(self, tmp_posts):
        posts = load_posts(tmp_posts)
        tagged = [p for p in posts if p.filename == "post-with-tags.md"][0]
        assert tagged.tags == ["systemd", "linux", "cron"]
        assert tagged.has_tags is True

    def test_no_tags_detected(self, tmp_posts):
        posts = load_posts(tmp_posts)
        no_tags = [p for p in posts if p.filename == "post-no-tags.md"][0]
        assert no_tags.tags == []
        assert no_tags.has_tags is False


class TestPrioritize:
    def test_no_tags_first(self, tmp_posts):
        posts = load_posts(tmp_posts)
        state = {"directory": str(tmp_posts), "posts": {}}
        ordered = prioritize(posts, state)
        # Posts sem tags devem vir primeiro
        assert not ordered[0].has_tags

    def test_empty_when_all_processed(self, tmp_posts):
        posts = load_posts(tmp_posts)
        state = {"directory": str(tmp_posts), "posts": {}}
        for p in posts:
            state["posts"][p.filename] = {
                "last_processed": "2099-01-01T00:00:00"
            }
            # Simular que todos têm tags
            p.has_tags = True
            p.tags = ["fake"]
        ordered = prioritize(posts, state)
        assert ordered == []

    def test_include_all(self, tmp_posts):
        posts = load_posts(tmp_posts)
        state = {"directory": str(tmp_posts), "posts": {}}
        for p in posts:
            state["posts"][p.filename] = {
                "last_processed": "2099-01-01T00:00:00"
            }
            p.has_tags = True
            p.tags = ["fake"]
        ordered = prioritize(posts, state, include_all=True)
        assert len(ordered) > 0


class TestCollectTagPool:
    def test_counts_tags(self, tmp_posts):
        posts = load_posts(tmp_posts)
        pool = collect_tag_pool(posts)
        assert pool["systemd"] == 1
        assert pool["linux"] == 1

    def test_empty_posts(self):
        pool = collect_tag_pool([])
        assert pool == {}


class TestFormatPoolForPrompt:
    def test_format(self):
        pool = {"linux": 10, "docker": 5}
        result = format_pool_for_prompt(pool)
        assert "linux (10)" in result
        assert "docker (5)" in result

    def test_limit(self):
        pool = {f"tag-{i}": i for i in range(200)}
        result = format_pool_for_prompt(pool, limit=3)
        assert result.count("(") == 3


class TestFindDuplicateTags:
    def test_detects_similar(self):
        pool = {"selfhosted": 5, "self-hosted": 3}
        dupes = find_duplicate_tags(pool)
        assert len(dupes) >= 1

    def test_detects_prefix(self):
        pool = {"docker": 5, "docker-compose": 3}
        dupes = find_duplicate_tags(pool)
        assert any("prefixo" in d[2] for d in dupes)

    def test_no_false_positives(self):
        pool = {"linux": 5, "docker": 3, "hugo": 2}
        dupes = find_duplicate_tags(pool)
        assert dupes == []
