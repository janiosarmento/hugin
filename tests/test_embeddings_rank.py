import numpy as np

from hugin.embeddings import EmbeddingIndex


def test_rank_by_text_orders_by_cosine(tmp_path, monkeypatch):
    index = EmbeddingIndex(tmp_path)
    index._model = object()  # skip real model loading
    index._cache["posts"] = {
        "/a.md": {"embedding": [1.0, 0.0]},
        "/b.md": {"embedding": [0.0, 1.0]},
        "/c.md": {"embedding": [0.7, 0.7]},
        "/no-vec.md": {},
    }
    seen = {}

    def fake_encode(text):
        seen["text"] = text
        return np.array([0.1, 1.0])

    monkeypatch.setattr(index, "_encode_single", fake_encode)
    assert index.rank_by_text("hello") == ["/b.md", "/c.md", "/a.md"]
    assert seen["text"] == "query: hello"


def test_rank_by_text_empty_prompt(tmp_path):
    assert EmbeddingIndex(tmp_path).rank_by_text("   ") == []
