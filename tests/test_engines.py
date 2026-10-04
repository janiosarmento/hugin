"""Testes do módulo engines."""

import secrets_resolver
from secrets_resolver.exceptions import SecretFileNotFound

import hugin.engines as engines_mod
from hugin.engines import Engine, _get_api_key, load_engines, load_fulcrum_echo_secret


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


class TestFulcrumEchoSecret:
    def _use(self, monkeypatch, tmp_path, content=None):
        path = tmp_path / "engines.toml"
        if content is not None:
            path.write_text(content)
        monkeypatch.setattr(engines_mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(engines_mod, "ENGINES_FILE", path)
        monkeypatch.setattr(secrets_resolver, "get_secret", lambda p: None)
        return path

    def test_new_file_gets_default(self, monkeypatch, tmp_path):
        self._use(monkeypatch, tmp_path)
        assert load_fulcrum_echo_secret() == "fulcrum_echo.key"

    def test_existing_file_is_migrated(self, monkeypatch, tmp_path):
        path = self._use(monkeypatch, tmp_path, '[a]\nurl = "http://x/v1"\nmodel = "m"\n')
        assert load_fulcrum_echo_secret() == "fulcrum_echo.key"
        assert "[fulcrum_echo]" in path.read_text()
        assert path.read_text().count("[fulcrum_echo]") == 1
        load_fulcrum_echo_secret()
        assert path.read_text().count("[fulcrum_echo]") == 1

    def test_custom_value(self, monkeypatch, tmp_path):
        self._use(monkeypatch, tmp_path, '[fulcrum_echo]\nsecret = "other.key"\n')
        assert load_fulcrum_echo_secret() == "other.key"

    def test_section_is_not_an_engine(self, monkeypatch, tmp_path):
        self._use(monkeypatch, tmp_path, '[a]\nurl = "http://x/v1"\nmodel = "m"\n')
        assert [e.id for e in load_engines()] == ["a"]


class TestMaskedKey:
    def test_repr_hides_tail(self):
        from hugin.engines import MaskedKey
        key = MaskedKey("sk-proj-abcdefghijklmnopqrstuvwxyz")
        assert "klmnop" not in repr(key)
        assert repr(key).startswith("'sk-proj-")

    def test_still_usable_as_str(self):
        from hugin.engines import MaskedKey
        key = MaskedKey("sk-123456789")
        assert f"Bearer {key}" == "Bearer sk-123456789"

    def test_engine_repr_is_masked(self):
        from hugin.engines import MaskedKey
        e = Engine("t", "http://x", "m", 30, MaskedKey("sk-secretsecretsecret"))
        assert "secretsecretsecret" not in repr(e)

    def test_short_key_not_fully_shown(self):
        from hugin.engines import MaskedKey
        assert "abcd" not in repr(MaskedKey("abcd"))

    def test_rich_pretty_masked(self):
        from rich.pretty import pretty_repr
        from hugin.engines import MaskedKey
        e = Engine("t", "http://x", "m", 30, MaskedKey("sk-secretsecretsecret"))
        assert "secretsecretsecret" not in pretty_repr(e)
