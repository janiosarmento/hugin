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
    parse_category,
    pick_category,
    write_with_fallback,
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
        return [make_post(tmp_path, f"{n}.md", d) for n, d in
                [("a", 1), ("b", 2), ("c", 3), ("d", 50), ("e", 60), ("f", 70), ("g", 80), ("h", 90)]]

    def _abs(self, tmp_path, *names):
        return [str((tmp_path / f"{n}.md").resolve()) for n in names]

    def test_recent_similar_and_random(self, tmp_path):
        posts = self._eight(tmp_path)
        picked = select_samples(posts, self._abs(tmp_path, "g", "f", "e"))
        names = [p.path.stem for p in picked]
        assert names[:3] == ["a", "b", "c"]
        assert len(names) == 6 and len(set(names)) == 6

    def test_similar_follow_ranking_and_skip_recent_and_random(self, tmp_path):
        posts = self._eight(tmp_path)
        rnd = posts[5]  # f
        ranked = self._abs(tmp_path, "a", "f", "g", "d", "e")
        names = [p.path.stem for p in select_samples(posts, ranked, random_pick=rnd)]
        # a is recent and f is the pinned random, so the 2 similar are g and d
        assert names == ["a", "b", "c", "g", "d", "f"]

    def test_pinned_random_is_kept(self, tmp_path):
        posts = self._eight(tmp_path)
        assert select_samples(posts, None, random_pick=posts[4])[-1].path.stem == "e"

    def test_invalid_pinned_random_is_redrawn(self, tmp_path):
        posts = self._eight(tmp_path)
        recent = posts[0]  # already a recent sample, not a valid random pick
        picked = select_samples(posts, None, random_pick=recent)
        assert len({p.path for p in picked}) == 6

    def test_no_ranking_falls_back_to_random(self, tmp_path):
        picked = select_samples(self._eight(tmp_path))
        assert len({p.path for p in picked}) == 6

    def test_random_varies_with_rng(self, tmp_path):
        import random

        posts = self._eight(tmp_path)
        seen = {select_samples(posts, None, random.Random(i))[-1].path.stem for i in range(40)}
        assert len(seen) > 2

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
    def test_plain_first_line(self):
        assert split_title("My Title\n\nBody here", "fb") == ("My Title", "Body here")

    def test_stray_markup_is_stripped(self):
        assert split_title("# My Title\n\nBody", "fb")[0] == "My Title"
        assert split_title("**My Title**\nBody", "fb")[0] == "My Title"
        assert split_title('"My Title"\nBody', "fb")[0] == "My Title"

    def test_overlong_first_line_uses_fallback(self):
        text = "word " * 60 + "\n\nmore"
        title, body = split_title(text, "fb")
        assert title == "fb" and body == text.strip()


def test_build_message_asks_for_title_on_first_line(tmp_path):
    msg = build_message([make_post(tmp_path, "a.md", 1)], "req")
    assert "title alone on the first line" in msg


def test_create_draft(tmp_path):
    path = create_draft(tmp_path, "Cats Rule\n\nMeow.", "about cats")
    assert path.name == "cats-rule.md"
    text = path.read_text()
    assert "draft: true" in text and "Meow." in text
    assert "title: Cats Rule" in text and "slug: cats-rule" in text
    again = create_draft(tmp_path, "Cats Rule\n\nMore.", "about cats")
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


CATS = ["Technology", "Cats & Pets", "Life"]


class TestCategory:
    def test_parse_exact_and_case_insensitive(self):
        assert parse_category("technology", CATS) == "Technology"
        assert parse_category('"Cats & Pets".', CATS) == "Cats & Pets"

    def test_parse_inside_sentence(self):
        assert parse_category("The best category is Life", CATS) == "Life"

    def test_parse_no_match(self):
        assert parse_category("Cooking", CATS) is None
        assert parse_category("", CATS) is None

    def test_pick_uses_system_llm_answer(self, monkeypatch):
        import hugin.llm as llm

        seen = {}

        async def fake(engine, prompt, system=None):
            seen["prompt"] = prompt
            return "Cats & Pets"

        monkeypatch.setattr(llm, "call_llm", fake)
        got = asyncio.run(pick_category(object(), "T", "body text", CATS))
        assert got == "Cats & Pets"
        assert "- Technology" in seen["prompt"] and "body text" in seen["prompt"]

    def test_pick_falls_back_to_first_on_unmatched_or_error(self, monkeypatch):
        import hugin.llm as llm

        async def nonsense(engine, prompt, system=None):
            return "no idea"

        async def boom(engine, prompt, system=None):
            raise RuntimeError("down")

        monkeypatch.setattr(llm, "call_llm", nonsense)
        assert asyncio.run(pick_category(object(), "T", "b", CATS)) == "Technology"
        monkeypatch.setattr(llm, "call_llm", boom)
        assert asyncio.run(pick_category(object(), "T", "b", CATS)) == "Technology"

    def test_pick_without_categories_is_none(self):
        assert asyncio.run(pick_category(object(), "T", "b", [])) is None

    def test_create_draft_writes_category(self, tmp_path):
        path = create_draft(tmp_path, "Title\n\nBody", "req", "Life")
        assert "- Life" in path.read_text()
        assert "TBD" not in path.read_text().split("categories:")[1].split("\n")[1]


class TestFallback:
    def _engine(self, available=True):
        from hugin.engines import Engine

        return Engine("sys", "https://x/v1", "m", 30, "key" if available else None)

    def _setup(self, monkeypatch, echo_result):
        import hugin.llm as llm

        calls = {}

        async def fake_echo(message, persona, key):
            if isinstance(echo_result, Exception):
                raise echo_result
            return echo_result

        async def fake_llm(engine, prompt, system=None):
            calls["engine"] = engine
            calls["prompt"] = prompt
            return calls.get("answer", " fallback text ")

        monkeypatch.setattr(echo, "get_api_key", lambda: "k")
        monkeypatch.setattr(echo, "ask_echo", fake_echo)
        monkeypatch.setattr(llm, "call_llm", fake_llm)
        return calls

    def test_echo_success_skips_fallback(self, monkeypatch):
        calls = self._setup(monkeypatch, "echo text")
        assert asyncio.run(write_with_fallback("m", "p", self._engine())) == ("echo text", None)
        assert calls == {}

    def test_echo_failure_delegates_to_system_llm(self, monkeypatch):
        calls = self._setup(monkeypatch, EchoError("Echo returned HTTP 402: out of credits"))
        text, reason = asyncio.run(write_with_fallback("the message", "p", self._engine()))
        assert text == "fallback text"
        assert "402" in reason and "out of credits" in reason
        assert calls["prompt"] == "the message"
        assert calls["engine"].timeout >= 300  # long-form writing gets a patient timeout

    def test_missing_key_also_falls_back(self, monkeypatch):
        calls = self._setup(monkeypatch, "unused")

        def no_key():
            raise EchoError("Echo API key not found")

        monkeypatch.setattr(echo, "get_api_key", no_key)
        text, reason = asyncio.run(write_with_fallback("m", "p", self._engine()))
        assert text == "fallback text" and "not found" in reason

    def test_no_usable_system_llm(self, monkeypatch):
        self._setup(monkeypatch, EchoError("boom"))
        for engine in (None, self._engine(available=False)):
            with pytest.raises(EchoError, match="boom.*no system LLM"):
                asyncio.run(write_with_fallback("m", "p", engine))

    def test_both_fail(self, monkeypatch):
        import hugin.llm as llm

        self._setup(monkeypatch, EchoError("echo down"))

        async def bad(engine, prompt, system=None):
            raise RuntimeError("llm down")

        monkeypatch.setattr(llm, "call_llm", bad)
        with pytest.raises(EchoError, match="echo down.*llm down"):
            asyncio.run(write_with_fallback("m", "p", self._engine()))


def test_http_error_detail_is_surfaced(monkeypatch):
    _patch_client(monkeypatch, lambda r: httpx.Response(402, json={"error": {"message": "out of credits"}}))
    with pytest.raises(EchoError, match="402: out of credits"):
        asyncio.run(ask_echo("m", "p", "k"))
