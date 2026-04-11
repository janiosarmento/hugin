"""Testes do módulo state."""

from datetime import datetime

from hugin.state import (
    get_last_processed,
    load_state,
    mark_processed,
    save_state,
)


class TestState:
    def test_load_empty(self, tmp_path):
        state = load_state(tmp_path)
        assert state["directory"] == str(tmp_path.resolve())
        assert state["posts"] == {}

    def test_save_and_load(self, tmp_path):
        state = load_state(tmp_path)
        mark_processed(state, "test.md")
        save_state(tmp_path, state)

        loaded = load_state(tmp_path)
        assert "test.md" in loaded["posts"]

    def test_mark_processed(self):
        state = {"directory": "/test", "posts": {}}
        mark_processed(state, "post.md")
        assert "post.md" in state["posts"]
        assert "last_processed" in state["posts"]["post.md"]

    def test_get_last_processed_none(self):
        state = {"directory": "/test", "posts": {}}
        assert get_last_processed(state, "nonexistent.md") is None

    def test_get_last_processed_exists(self):
        state = {
            "directory": "/test",
            "posts": {
                "post.md": {"last_processed": "2026-04-10T14:30:00"}
            },
        }
        result = get_last_processed(state, "post.md")
        assert isinstance(result, datetime)
        assert result.year == 2026
