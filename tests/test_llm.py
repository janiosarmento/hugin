"""Testes do módulo llm (parser de resposta)."""

import pytest

from hugin.llm import build_prompt, parse_response


class TestParseResponse:
    def test_clean_json(self):
        assert parse_response('["tag-one", "tag-two"]') == ["tag-one", "tag-two"]

    def test_with_code_fences(self):
        text = '```json\n["hugo", "linux"]\n```'
        assert parse_response(text) == ["hugo", "linux"]

    def test_with_leading_text(self):
        text = 'Sure! Here are the tags:\n["docker", "selfhosted"]'
        assert parse_response(text) == ["docker", "selfhosted"]

    def test_with_trailing_text(self):
        text = '["docker", "linux"]\nHope that helps!'
        assert parse_response(text) == ["docker", "linux"]

    def test_fallback_regex(self):
        text = 'tags: "docker", "linux", "hugo"'
        assert parse_response(text) == ["docker", "linux", "hugo"]

    def test_empty_array(self):
        assert parse_response("[]") == []

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_response("no tags here at all")

    def test_whitespace_around(self):
        assert parse_response('  ["tag"]  ') == ["tag"]

    def test_nested_code_fence_with_language(self):
        text = '```JSON\n["a", "b"]\n```'
        assert parse_response(text) == ["a", "b"]


class TestBuildPrompt:
    def test_includes_pool(self):
        prompt = build_prompt({}, "content", "linux (5), docker (3)")
        assert "linux (5), docker (3)" in prompt

    def test_includes_content(self):
        prompt = build_prompt({}, "meu conteúdo aqui", "")
        assert "meu conteúdo aqui" in prompt

    def test_truncates_long_content(self):
        long_content = "x" * 20000
        metadata = {"title": "Meu Post", "description": "Descrição"}
        prompt = build_prompt(metadata, long_content, "")
        assert "Title: Meu Post" in prompt
        assert "Description: Descrição" in prompt
        assert len(prompt) < len(long_content)
