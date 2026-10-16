"""A downloaded embedding model loads from disk, without asking the Hub (which may rate-limit)."""

import huggingface_hub
import pytest
from huggingface_hub.errors import LocalEntryNotFoundError

from hugin.embeddings import _local_snapshot


@pytest.fixture
def snapshot(tmp_path):
    path = tmp_path / "snapshot"
    (path / "onnx").mkdir(parents=True)
    (path / "modules.json").write_text("[]")
    (path / "onnx" / "model.onnx").write_bytes(b"")
    return path


def _fake_download(monkeypatch, result):
    asked = []

    def fake(repo_id, local_files_only=False, **_):
        asked.append((repo_id, local_files_only))
        if isinstance(result, Exception):
            raise result
        return str(result)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake)
    return asked


def test_cached_model_resolves_to_its_directory_offline(monkeypatch, snapshot):
    asked = _fake_download(monkeypatch, snapshot)

    assert _local_snapshot("org/model") == str(snapshot)
    assert asked == [("org/model", True)]


def test_bare_name_is_looked_up_under_sentence_transformers(monkeypatch, snapshot):
    asked = _fake_download(monkeypatch, snapshot)

    _local_snapshot("paraphrase-multilingual-MiniLM-L12-v2")

    assert asked == [("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", True)]


def test_model_not_downloaded_falls_back_to_the_hub(monkeypatch):
    _fake_download(monkeypatch, LocalEntryNotFoundError("not cached"))

    assert _local_snapshot("org/model") is None


def test_partial_download_falls_back_to_the_hub(monkeypatch, snapshot):
    (snapshot / "onnx" / "model.onnx").unlink()
    _fake_download(monkeypatch, snapshot)

    assert _local_snapshot("org/model") is None


def test_local_directory_is_left_to_sentence_transformers(monkeypatch, snapshot):
    asked = _fake_download(monkeypatch, snapshot)

    assert _local_snapshot(str(snapshot)) is None
    assert asked == []
