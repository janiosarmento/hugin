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
    has_enough_samples,
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
    def _eight(self, tmp_path):
        return [
            make_post(tmp_path, "a.md", 1),
            make_post(tmp_path, "b.md", 2),
            make_post(tmp_path, "c.md", 3),
            make_post(tmp_path, "big1.md", 50, size=500),
            make_post(tmp_path, "big2.md", 60, size=900),
            make_post(tmp_path, "s1.md", 70, size=20),
            make_post(tmp_path, "s2.md", 80, size=30),
            make_post(tmp_path, "s3.md", 90, size=40),
        ]

    def test_recent_largest_and_one_random(self, tmp_path):
        picked = select_samples(self._eight(tmp_path))
        names = [p.filename for p in picked]
        assert names[:5] == ["a.md", "b.md", "c.md", "big2.md", "big1.md"]
        assert len(names) == 6 and len(set(names)) == 6
        assert names[5] in {"s1.md", "s2.md", "s3.md"}

    def test_random_pick_varies_with_rng(self, tmp_path):
        import random

        posts = self._eight(tmp_path)
        seen = {select_samples(posts, random.Random(i))[5].filename for i in range(30)}
        assert seen == {"s1.md", "s2.md", "s3.md"}

    def test_largest_skips_already_recent(self, tmp_path):
        posts = [
            make_post(tmp_path, "huge-recent.md", 1, size=9000),
            make_post(tmp_path, "b.md", 2),
            make_post(tmp_path, "c.md", 3),
            make_post(tmp_path, "old.md", 40, size=100),
            make_post(tmp_path, "older.md", 41, size=50),
            make_post(tmp_path, "oldest.md", 42, size=5),
        ]
        names = [p.filename for p in select_samples(posts)]
        assert names.count("huge-recent.md") == 1
        assert len(set(names)) == 6

    def test_drafts_and_future_posts_excluded(self, tmp_path):
        posts = [
            make_post(tmp_path, "draft.md", 1, draft=True),
            make_post(tmp_path, "future.md", -5),
            make_post(tmp_path, "ok.md", 3),
        ]
        assert [p.filename for p in select_samples(posts)] == ["ok.md"]

    def test_has_enough_samples(self, tmp_path):
        posts = self._eight(tmp_path)
        assert has_enough_samples(posts)
        assert has_enough_samples(posts[:6])
        assert not has_enough_samples(posts[:5])
        posts[0].metadata["draft"] = True
        assert not has_enough_samples(posts[:6])


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
