import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

import hugin.echo as echo
from hugin.echo import (
    EchoError,
    ask_echo,
    build_message,
    create_draft,
    select_samples,
    split_title,
)
from hugin.scanner import Post


def make_post(tmp_path: Path, name: str, days_ago: int, size: int = 10, draft: bool = False) -> Post:
    path = tmp_path / name
    body = "x" * size
    path.write_text(body)
    return Post(
        path=path,
        metadata={"title": name, "draft": draft},
        content=body,
        has_tags=False,
        date=datetime.now() - timedelta(days=days_ago),
    )


class TestSelectSamples:
    def test_three_recent_plus_two_largest(self, tmp_path):
        posts = [
            make_post(tmp_path, "a.md", 1),
            make_post(tmp_path, "b.md", 2),
            make_post(tmp_path, "c.md", 3),
            make_post(tmp_path, "big1.md", 50, size=500),
            make_post(tmp_path, "big2.md", 60, size=900),
            make_post(tmp_path, "small.md", 70, size=20),
        ]
        names = [p.filename for p in select_samples(posts)]
        assert names == ["a.md", "b.md", "c.md", "big2.md", "big1.md"]

    def test_largest_skips_already_recent(self, tmp_path):
        posts = [
            make_post(tmp_path, "huge-recent.md", 1, size=9000),
            make_post(tmp_path, "b.md", 2),
            make_post(tmp_path, "c.md", 3),
            make_post(tmp_path, "old.md", 40, size=100),
            make_post(tmp_path, "older.md", 41, size=50),
        ]
        names = [p.filename for p in select_samples(posts)]
        assert names.count("huge-recent.md") == 1
        assert len(names) == 5

    def test_drafts_and_future_posts_excluded(self, tmp_path):
        posts = [
            make_post(tmp_path, "draft.md", 1, draft=True),
            make_post(tmp_path, "future.md", -5),
            make_post(tmp_path, "ok.md", 3),
        ]
        assert [p.filename for p in select_samples(posts)] == ["ok.md"]

    def test_fewer_than_five_posts(self, tmp_path):
        posts = [make_post(tmp_path, "a.md", 1), make_post(tmp_path, "b.md", 2)]
        assert len(select_samples(posts)) == 2


def test_build_message_wraps_posts_and_ends_with_request(tmp_path):
    p = make_post(tmp_path, "a.md", 1)
    msg = build_message([p], "  Write about cats  ")
    assert "<post>\n# a.md" in msg and "</post>" in msg
    assert msg.endswith("Write about cats")


class TestSplitTitle:
    def test_heading_is_extracted(self):
        assert split_title("# My Title\n\nBody here", "fb") == ("My Title", "Body here")

    def test_no_heading_uses_fallback(self):
        assert split_title("Just text", "fb") == ("fb", "Just text")


def test_create_draft(tmp_path):
    path = create_draft(tmp_path, "# Cats Rule\n\nMeow.", "about cats")
    assert path.name == "cats-rule.md"
    text = path.read_text()
    assert "draft: true" in text and "Meow." in text
    again = create_draft(tmp_path, "# Cats Rule\n\nMore.", "about cats")
    assert again.name == "cats-rule-1.md"


def _patch_client(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(
        echo.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )


class TestAskEcho:
    def test_success_and_request_shape(self, monkeypatch):
        seen = {}

        def handler(request):
            seen["auth"] = request.headers["authorization"]
            seen["body"] = request.read()
            return httpx.Response(200, json={"choices": [{"message": {"content": " hi "}}]})

        _patch_client(monkeypatch, handler)
        assert asyncio.run(ask_echo("msg", "Jane Doe", "k123")) == "hi"
        assert seen["auth"] == "Bearer k123"
        import json
        body = json.loads(seen["body"])
        assert body["persona"] == "Jane Doe"
        assert body["model"] == "echo"
        assert body["messages"] == [{"role": "user", "content": "msg"}]

    @pytest.mark.parametrize("status,fragment", [(401, "401"), (500, "500")])
    def test_http_errors(self, monkeypatch, status, fragment):
        _patch_client(monkeypatch, lambda r: httpx.Response(status))
        with pytest.raises(EchoError, match=fragment):
            asyncio.run(ask_echo("m", "p", "k"))

    def test_malformed_body(self, monkeypatch):
        _patch_client(monkeypatch, lambda r: httpx.Response(200, json={"nope": 1}))
        with pytest.raises(EchoError, match="Unexpected"):
            asyncio.run(ask_echo("m", "p", "k"))
