"""Testes do módulo normalizer."""

from hugin.normalizer import normalize_tag, normalize_tags


class TestNormalizeTag:
    def test_lowercase(self):
        assert normalize_tag("Docker") == "docker"

    def test_spaces_to_hyphens(self):
        assert normalize_tag("self hosted") == "self-hosted"

    def test_preserves_accents(self):
        assert normalize_tag("Automação") == "automação"
        assert normalize_tag("Programação Web") == "programação-web"

    def test_removes_articles_pt(self):
        assert normalize_tag("O Melhor Tag") == "melhor-tag"
        assert normalize_tag("Uma Dica") == "dica"

    def test_removes_articles_en(self):
        assert normalize_tag("The Best Tag") == "best-tag"
        assert normalize_tag("A Quick Tip") == "quick-tip"

    def test_truncates_to_3_words(self):
        assert normalize_tag("one two three four five") == "one-two-three"

    def test_strips_whitespace(self):
        assert normalize_tag("  docker  ") == "docker"

    def test_removes_duplicate_hyphens(self):
        assert normalize_tag("self--hosted") == "self-hosted"

    def test_empty_string(self):
        assert normalize_tag("") == ""


class TestNormalizeTags:
    def test_dedup_against_pool(self):
        pool = {"selfhosted": 5}
        result = normalize_tags(["Selfhosted"], [], pool)
        assert result == ["selfhosted"]

    def test_dedup_against_existing(self):
        result = normalize_tags(["docker"], ["docker"], {})
        assert result == []

    def test_dedup_within_batch(self):
        result = normalize_tags(["docker", "Docker"], [], {})
        assert result == ["docker"]

    def test_filters_empty(self):
        result = normalize_tags(["", "  ", "docker"], [], {})
        assert result == ["docker"]

    def test_prefers_pool_form(self):
        pool = {"selfHosted": 3}
        result = normalize_tags(["selfhosted"], [], pool)
        assert result == ["selfHosted"]
