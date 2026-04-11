"""Testes do módulo engines."""

import os

from hugin.engines import Engine, _get_api_key


class TestEngine:
    def test_is_local_localhost(self):
        e = Engine("test", "http://localhost:5555/v1", "m", 30, None)
        assert e.is_local is True

    def test_is_local_127(self):
        e = Engine("test", "http://127.0.0.1:5555/v1", "m", 30, None)
        assert e.is_local is True

    def test_is_not_local(self):
        e = Engine("test", "https://api.openai.com/v1", "m", 30, None)
        assert e.is_local is False

    def test_available_with_key(self):
        e = Engine("test", "https://api.openai.com/v1", "m", 30, "sk-123")
        assert e.available is True

    def test_available_local_no_key(self):
        e = Engine("test", "http://localhost:5555/v1", "m", 30, None)
        assert e.available is True

    def test_unavailable_remote_no_key(self):
        e = Engine("test", "https://api.openai.com/v1", "m", 30, None)
        assert e.available is False


class TestGetApiKey:
    def test_reads_env_var(self, monkeypatch):
        monkeypatch.setenv("TEST_API_KEY", "sk-abc")
        assert _get_api_key("test") == "sk-abc"

    def test_returns_none_if_missing(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_API_KEY", raising=False)
        assert _get_api_key("nonexistent") is None

    def test_returns_none_if_empty(self, monkeypatch):
        monkeypatch.setenv("EMPTY_API_KEY", "")
        assert _get_api_key("empty") is None
