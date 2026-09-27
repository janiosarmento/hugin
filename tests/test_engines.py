"""Testes do módulo engines."""

import secrets_resolver
from secrets_resolver.exceptions import SecretFileNotFound

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
    def test_reads_secret_from_vault(self, monkeypatch):
        monkeypatch.setattr(secrets_resolver, "get_secret", lambda path: "sk-abc")
        assert _get_api_key("test") == "sk-abc"

    def test_returns_none_if_missing(self, monkeypatch):
        def _raise(path):
            raise SecretFileNotFound(f"no secret file for {path}")

        monkeypatch.setattr(secrets_resolver, "get_secret", _raise)
        assert _get_api_key("nonexistent") is None

    def test_uses_custom_secret_path(self, monkeypatch):
        seen = {}

        def _fake_get_secret(path):
            seen["path"] = path
            return "sk-custom"

        monkeypatch.setattr(secrets_resolver, "get_secret", _fake_get_secret)
        assert _get_api_key("test", secret="custom.path") == "sk-custom"
        assert seen["path"] == "custom.path"
