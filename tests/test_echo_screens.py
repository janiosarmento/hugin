import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from textual.app import App

import hugin.echo as echo_mod
import hugin.tui.echo_draft as ed
from hugin.echo import EchoError
from hugin.scanner import Post


def _posts(tmp_path):
    out = []
    for i in range(7):
        path = tmp_path / f"p{i}.md"
        path.write_text("body")
        out.append(Post(path, {"title": f"P{i}"}, "body", False,
                        date=datetime.now() - timedelta(days=i + 1)))
    return out


def _run(screen_factory, keys_fn):
    result = {}

    async def go():
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(screen_factory(), lambda r: result.setdefault("v", r))
            await pilot.pause()
            await keys_fn(app, pilot)
            await pilot.pause(0.3)

    asyncio.run(go())
    return result.get("v", "unset")


def test_prompt_screen_submits_text(monkeypatch):
    monkeypatch.setattr(ed, "_last_writer", "echo")
    async def keys(app, pilot):
        await pilot.press(*"hello", "ctrl+s")

    assert _run(ed.EchoPromptScreen, keys) == ("hello", "echo")


def test_prompt_screen_rejects_empty_and_cancels():
    async def keys(app, pilot):
        await pilot.press("ctrl+s")
        assert isinstance(app.screen, ed.EchoPromptScreen)
        await pilot.press("escape")

    assert _run(ed.EchoPromptScreen, keys) is None


def test_wait_screen_creates_draft(tmp_path, monkeypatch):
    seen = {}

    async def fake_ask(message, persona, key):
        seen.update(message=message, persona=persona, key=key)
        return "Echo Title\n\nEcho body."

    monkeypatch.setattr(echo_mod, "ask_echo", fake_ask)
    monkeypatch.setattr(echo_mod, "get_api_key", lambda: "k")
    monkeypatch.setattr(ed, "load_fulcrum_echo_persona", lambda: "Jane Doe")

    async def keys(app, pilot):
        await pilot.pause(0.3)

    path = _run(lambda: ed.EchoWaitScreen("write cats", _posts(tmp_path), tmp_path, None), keys)
    assert path.name == "echo-title.md"
    assert "Echo body." in path.read_text()
    assert seen["persona"] == "Jane Doe" and seen["message"].count("<post>") == 7
    assert seen["message"].endswith("write cats")


def test_wait_screen_error_returns_none(tmp_path, monkeypatch):
    def boom():
        raise EchoError("no key")

    monkeypatch.setattr(echo_mod, "get_api_key", boom)
    monkeypatch.setattr(ed, "load_fulcrum_echo_persona", lambda: "x")

    async def keys(app, pilot):
        await pilot.pause(0.3)

    assert _run(lambda: ed.EchoWaitScreen("q", _posts(tmp_path), tmp_path, None), keys) is None
    assert len(list(tmp_path.glob("*.md"))) == 7


def test_h_key_creates_draft_in_main_screen(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from textual.widgets import DataTable

    from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
    from hugin.engines import Engine
    from hugin.tui.review import HuginScreen

    async def fake_ask(message, persona, key):
        return "From Echo\n\nText."

    monkeypatch.setattr(echo_mod, "ask_echo", fake_ask)
    monkeypatch.setattr(echo_mod, "get_api_key", lambda: "k")
    monkeypatch.setattr(ed, "load_fulcrum_echo_persona", lambda: "Jane Doe")

    posts = _posts(tmp_path)
    site = MagicMock()
    site.post_url.return_value = "/x"
    site.warnings = []

    index = MagicMock()
    index.has_no_outgoing.return_value = False
    index.get_post_url.return_value = "/x"
    index.get_link_keywords.return_value = ""

    class Host(App):
        def on_mount(self):
            self.push_screen(HuginScreen(
                posts=posts, all_posts=list(posts),
                engine=Engine("t", "http://localhost/v1", "m", 30, None),
                pool={}, state={}, directory=tmp_path,
                config=HuginConfig(LinksConfig(), EmbeddingsConfig(), FrontmatterConfig()), site=site, index=index,
            ))

    async def go():
        app = Host()
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            assert isinstance(app.screen, ed.EchoPromptScreen)
            await pilot.press(*"cats", "ctrl+s")
            await pilot.pause(0.5)
            assert isinstance(app.screen, HuginScreen)
            assert (tmp_path / "from-echo.md").exists()
            assert app.screen.query_one("#post-table", DataTable).row_count == 8

    asyncio.run(go())


def test_h_key_warns_without_enough_posts(tmp_path):
    from unittest.mock import MagicMock

    from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
    from hugin.engines import Engine
    from hugin.tui.review import HuginScreen

    posts = _posts(tmp_path)[:6]
    site = MagicMock()
    site.post_url.return_value = "/x"
    site.warnings = []
    index = MagicMock()
    index.has_no_outgoing.return_value = False
    index.get_post_url.return_value = "/x"
    index.get_link_keywords.return_value = ""
    notes = []

    class Host(App):
        def on_mount(self):
            self.push_screen(HuginScreen(
                posts=posts, all_posts=list(posts),
                engine=Engine("t", "http://localhost/v1", "m", 30, None),
                pool={}, state={}, directory=tmp_path,
                config=HuginConfig(LinksConfig(), EmbeddingsConfig(), FrontmatterConfig()),
                site=site, index=index,
            ))

        def notify(self, message, **kw):
            notes.append(message)

    async def go():
        app = Host()
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            assert isinstance(app.screen, HuginScreen)

    asyncio.run(go())
    assert any("Not enough published posts" in n for n in notes)


def test_wait_screen_assigns_category_from_system_llm(tmp_path, monkeypatch):
    import hugin.llm as llm

    (tmp_path / ".pages.yml").write_text(
        "content:\n  - name: post\n    fields:\n      - name: categories\n"
        "        type: select\n        options:\n          values:\n            - label: Life Stuff\n              value: life\n"
    )

    async def fake_ask(message, persona, key):
        return "Title\n\nBody"

    async def fake_llm(engine, prompt, system=None):
        return "life"

    monkeypatch.setattr(echo_mod, "ask_echo", fake_ask)
    monkeypatch.setattr(llm, "call_llm", fake_llm)
    monkeypatch.setattr(echo_mod, "get_api_key", lambda: "k")
    monkeypatch.setattr(ed, "load_fulcrum_echo_persona", lambda: "x")

    async def keys(app, pilot):
        await pilot.pause(0.3)

    path = _run(lambda: ed.EchoWaitScreen("q", _posts(tmp_path), tmp_path, object()), keys)
    text = path.read_text()
    assert "- life" in text and "TBD" not in text.split("description")[0].split("categories:")[1]


def test_wait_screen_uses_semantic_ranking(tmp_path, monkeypatch):
    seen = {}

    class FakeIndex:
        def rank_by_text(self, text):
            seen["query"] = text
            return [str((tmp_path / "p5.md").resolve())]

    async def fake_ask(message, persona, key):
        seen["message"] = message
        return "T\n\nB"

    monkeypatch.setattr(echo_mod, "ask_echo", fake_ask)
    monkeypatch.setattr(echo_mod, "get_api_key", lambda: "k")
    monkeypatch.setattr(ed, "load_fulcrum_echo_persona", lambda: "x")
    posts = _posts(tmp_path)  # p0..p5, p0 newest; p5 oldest and smallest tie

    async def keys(app, pilot):
        await pilot.pause(0.3)

    _run(lambda: ed.EchoWaitScreen("about cats", posts, tmp_path, None, FakeIndex()), keys)
    assert seen["query"] == "about cats"
    assert seen["message"].count("<post>") == 7


def test_wait_screen_survives_index_failure(tmp_path, monkeypatch):
    class BrokenIndex:
        def rank_by_text(self, text):
            raise RuntimeError("model missing")

    async def fake_ask(message, persona, key):
        return "T\n\nB"

    monkeypatch.setattr(echo_mod, "ask_echo", fake_ask)
    monkeypatch.setattr(echo_mod, "get_api_key", lambda: "k")
    monkeypatch.setattr(ed, "load_fulcrum_echo_persona", lambda: "x")

    async def keys(app, pilot):
        await pilot.pause(0.3)

    path = _run(lambda: ed.EchoWaitScreen("q", _posts(tmp_path), tmp_path, None, BrokenIndex()), keys)
    assert path is not None and path.name == "t.md"


def test_wait_screen_falls_back_and_warns(tmp_path, monkeypatch):
    import hugin.llm as llm
    from hugin.engines import Engine

    async def echo_down(message, persona, key):
        raise EchoError("Echo returned HTTP 402: out of credits")

    async def fake_llm(engine, prompt, system=None):
        return "System Title\n\nSystem body" if "Pick the single best" not in prompt else "x"

    monkeypatch.setattr(echo_mod, "ask_echo", echo_down)
    monkeypatch.setattr(echo_mod, "get_api_key", lambda: "k")
    monkeypatch.setattr(llm, "call_llm", fake_llm)
    monkeypatch.setattr(ed, "load_fulcrum_echo_persona", lambda: "x")
    notes = []
    engine = Engine("sys", "https://x/v1", "m", 30, "key")

    result = {}

    async def go():
        class Host(App):
            def notify(self, message, **kw):
                notes.append(message)

        app = Host()
        async with app.run_test() as pilot:
            app.push_screen(
                ed.EchoWaitScreen("q", _posts(tmp_path), tmp_path, engine),
                lambda r: result.setdefault("v", r),
            )
            await pilot.pause(0.5)

    asyncio.run(go())
    assert result["v"].name == "system-title.md"
    assert "System body" in result["v"].read_text()
    assert any("402" in n and "sys" in n for n in notes)


def test_prompt_screen_lists_sample_titles_and_updates_similar(tmp_path, monkeypatch):
    monkeypatch.setattr(echo_mod, "N_SIMILAR", 3)
    monkeypatch.setattr(echo_mod, "N_RANDOM", 1)
    posts = _posts(tmp_path)  # P0 newest ... P5 oldest
    odd = Post(tmp_path / "odd.md", {"title": "Odd [markup] title"}, "x", False,
               date=datetime.now() - timedelta(days=99))
    odd.path.write_text("x")
    posts.append(odd)
    monkeypatch.setattr(ed, "draw_random", lambda rest: posts[5])  # P5 is the random pick
    monkeypatch.setattr(ed, "N_RANDOM", 1)

    class FakeIndex:
        def rank_by_text(self, text):
            return [str(p.path.resolve()) for p in (odd, posts[5], posts[4], posts[3])]

    class Fast(ed.EchoPromptScreen):
        DEBOUNCE_SECONDS = 0.05

    async def go():
        app = App()
        async with app.run_test() as pilot:
            screen = Fast(posts, FakeIndex())
            app.push_screen(screen)
            await pilot.pause()
            shown = str(screen.query_one("#echo-samples").render())
            assert shown.count("latest") == 3 and "P0" in shown
            assert "picked when you send" in shown
            assert shown.count("random") == 1 and "P5" in shown
            assert screen.random_pick is posts[5]
            await pilot.press(*"cats")
            await pilot.pause(0.4)
            shown = str(screen.query_one("#echo-samples").render())
            # odd + P4 + P3 (P5 is the pinned random, so it is skipped in the ranking)
            assert shown.count("similar") == 3
            assert "Odd [markup] title" in shown and "P4" in shown and "P3" in shown
            assert "picked when you send" not in shown

    asyncio.run(go())


def test_prompt_screen_f2_picks_system_writer_and_remembers(monkeypatch):
    from hugin.engines import Engine

    monkeypatch.setattr(ed, "_last_writer", "echo")
    engine = Engine("sys", "https://x/v1", "big-model", 30, "k")

    async def keys(app, pilot):
        await pilot.press(*"hi", "f2", "ctrl+s")

    assert _run(lambda: ed.EchoPromptScreen([], None, engine), keys) == ("hi", "system")
    assert ed._last_writer == "system"

    # next time the selector starts on the remembered writer
    async def keys2(app, pilot):
        await pilot.press(*"again", "ctrl+s")

    assert _run(lambda: ed.EchoPromptScreen([], None, engine), keys2) == ("again", "system")
    assert "sys / big-model" in ed.EchoPromptScreen([], None, engine)._system_label()


def test_wait_screen_system_writer_skips_echo(tmp_path, monkeypatch):
    import hugin.llm as llm
    from hugin.engines import Engine

    def echo_must_not_run(*a, **k):
        raise AssertionError("Echo must not be called")

    async def fake_llm(engine, prompt, system=None):
        return "Direct Title\n\nDirect body" if "Pick the single best" not in prompt else "x"

    monkeypatch.setattr(echo_mod, "ask_echo", echo_must_not_run)
    monkeypatch.setattr(echo_mod, "get_api_key", echo_must_not_run)
    monkeypatch.setattr(llm, "call_llm", fake_llm)
    notes = []
    engine = Engine("sys", "https://x/v1", "big-model", 30, "key")
    result = {}

    async def go():
        class Host(App):
            def notify(self, message, **kw):
                notes.append(message)

        app = Host()
        async with app.run_test() as pilot:
            app.push_screen(
                ed.EchoWaitScreen("q", _posts(tmp_path), tmp_path, engine, writer="system"),
                lambda r: result.setdefault("v", r),
            )
            await pilot.pause(0.5)

    asyncio.run(go())
    assert result["v"].name == "direct-title.md"
    assert "Written by sys / big-model" in notes


def test_wait_screen_system_writer_without_engine_fails_cleanly(tmp_path):
    async def keys(app, pilot):
        await pilot.pause(0.3)

    assert _run(lambda: ed.EchoWaitScreen("q", _posts(tmp_path), tmp_path, None, writer="system"), keys) is None


def test_prompt_screen_without_random_sample(tmp_path):
    posts = _posts(tmp_path)

    async def go():
        app = App()
        async with app.run_test() as pilot:
            screen = ed.EchoPromptScreen(posts, None)
            app.push_screen(screen)
            await pilot.pause()
            shown = str(screen.query_one("#echo-samples").render())
            assert "random" not in shown and screen.random_pick is None
            assert "4 closest to your prompt" in shown
            hint = str(screen.query_one("#echo-hint").render())
            assert "3 latest, 4 similar to your prompt)" in hint

    asyncio.run(go())


def test_R_key_refactors_post_and_goto_original(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from textual.widgets import Button, DataTable

    from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
    from hugin.engines import Engine
    from hugin.tui.review import HuginScreen

    seen = {}

    async def fake_ask(message, persona, key):
        seen["message"] = message
        return "Rewritten\n\nNew text."

    monkeypatch.setattr(echo_mod, "ask_echo", fake_ask)
    monkeypatch.setattr(echo_mod, "get_api_key", lambda: "k")
    monkeypatch.setattr(ed, "load_fulcrum_echo_persona", lambda: "Jane Doe")

    posts = _posts(tmp_path)
    posts[0].metadata.update(categories=["journaling"], thumbnail="/images/cover.avif", translationKey="p0-key")
    site = MagicMock()
    site.post_url.return_value = "/x"
    site.warnings = []
    index = MagicMock()
    index.has_no_outgoing.return_value = False
    index.get_post_url.return_value = "/x"
    index.get_link_keywords.return_value = ""

    class Host(App):
        def on_mount(self):
            self.push_screen(HuginScreen(
                posts=posts, all_posts=list(posts),
                engine=Engine("t", "http://localhost/v1", "m", 30, None),
                pool={}, state={}, directory=tmp_path,
                config=HuginConfig(LinksConfig(), EmbeddingsConfig(), FrontmatterConfig()), site=site, index=index,
            ))

    async def go():
        app = Host()
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            screen = app.screen
            goto = screen.query_one("#btn-goto-original", Button)
            assert goto.has_class("hidden")  # p0 is not a refactor
            await pilot.press("R")
            await pilot.pause()
            assert isinstance(app.screen, ed.EchoPromptScreen)
            await pilot.press(*"tighter", "ctrl+s")
            await pilot.pause(0.5)
            assert isinstance(app.screen, HuginScreen)
            draft = (tmp_path / "rewritten.md").read_text()
            assert "refactor_of: p0.md" in draft and "prompt: tighter" in draft
            assert "thumbnail: /images/cover.avif" in draft and "- journaling" in draft
            assert "translationKey: p0-key" in draft
            assert seen["message"].count("<post>") == 6 and "<original>" in seen["message"]
            assert screen.query_one("#post-table", DataTable).row_count == 8
            assert screen.current_index == 0
            assert not goto.has_class("hidden")
            screen.action_goto_original()
            await pilot.pause()
            assert screen.posts[screen.current_index].filename == "p0.md"
            assert goto.has_class("hidden")

    asyncio.run(go())


def test_R_key_warns_without_enough_posts(tmp_path):
    from hugin.echo import has_enough_samples

    posts = _posts(tmp_path)[:6]
    assert not has_enough_samples(posts, posts[0])


def test_copy_body_button_copies_markdown_without_frontmatter(tmp_path):
    from unittest.mock import MagicMock

    from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
    from hugin.engines import Engine
    from hugin.tui.review import HuginScreen

    posts = _posts(tmp_path)
    posts[0].path.write_text("---\ntitle: P0\n---\n\nHello **body**.\n")
    site = MagicMock()
    site.post_url.return_value = "/x"
    site.warnings = []
    index = MagicMock()
    index.has_no_outgoing.return_value = False
    index.get_post_url.return_value = "/x"
    index.get_link_keywords.return_value = ""
    copied = []

    class Host(App):
        def on_mount(self):
            self.push_screen(HuginScreen(
                posts=posts, all_posts=list(posts),
                engine=Engine("t", "http://localhost/v1", "m", 30, None),
                pool={}, state={}, directory=tmp_path,
                config=HuginConfig(LinksConfig(), EmbeddingsConfig(), FrontmatterConfig()),
                site=site, index=index,
            ))

        def copy_to_clipboard(self, text):
            copied.append(text)

    async def go():
        app = Host()
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await pilot.click("#btn-copy-body")
            await pilot.pause()

    asyncio.run(go())
    assert copied == ["Hello **body**."]


def test_u_key_sends_similar_post_titles_to_the_llm(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    import hugin.tui.review as review
    from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
    from hugin.engines import Engine
    from hugin.tui.review import HuginScreen

    prompts = []

    async def fake_llm(engine, prompt, **kw):
        prompts.append(prompt)
        return '["A brand new angle"]'

    monkeypatch.setattr(review, "call_llm", fake_llm)

    posts = _posts(tmp_path)
    site = MagicMock()
    site.post_url.return_value = "/x"
    site.warnings = []
    index = MagicMock()
    index.has_no_outgoing.return_value = False
    index.get_post_url.return_value = "/x"
    index.get_link_keywords.return_value = ""
    index.find_similar.return_value = [{"title": "Litter box guide"}, {"title": "Cat food"}]
    index._cache = {}

    class Host(App):
        def on_mount(self):
            self.push_screen(HuginScreen(
                posts=posts, all_posts=list(posts),
                engine=Engine("t", "http://localhost/v1", "m", 30, None),
                pool={}, state={}, directory=tmp_path,
                config=HuginConfig(LinksConfig(), EmbeddingsConfig(), FrontmatterConfig()),
                site=site, index=index,
            ))

    async def go():
        app = Host()
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await pilot.press("u")
            await pilot.pause(0.5)

    asyncio.run(go())
    assert index.find_similar.call_args.kwargs["n"] == 5
    assert "- Litter box guide\n- Cat food" in prompts[0]


class TestFindAnchors:
    """Anchor finding: slug matches skip the LLM, retries run concurrently."""

    BODY = (
        "I bought a litter box last week. My cat loves the automatic "
        "feeder and the water fountain too.\n"
    )

    def _run(self, monkeypatch, candidates, llm):
        from types import SimpleNamespace

        import hugin.tui.review as review
        from hugin.config import LinksConfig
        from hugin.scanner import Post

        monkeypatch.setattr(review, "call_llm", llm)
        fake = SimpleNamespace(
            engine=None, config=SimpleNamespace(links=LinksConfig()), _anchor_stats={},
        )
        post = Post(Path("x.md"), {"title": "X"}, self.BODY, False)
        result = asyncio.run(review.HuginScreen._find_anchors(fake, post, candidates, set()))
        return result, fake

    def test_multiword_slug_match_skips_the_llm(self, monkeypatch):
        calls = []

        async def llm(engine, prompt, **kw):
            calls.append(prompt)
            return "[]"

        result, fake = self._run(
            monkeypatch, [{"title": "Litter", "url": "/litter-box/"}], llm,
        )
        assert result == [{"anchor_text": "litter box", "target_url": "/litter-box/"}]
        assert calls == []
        assert fake._anchor_stats["llm_asked"] == 0

    def test_only_unsettled_candidates_go_to_the_llm(self, monkeypatch):
        prompts = []

        async def llm(engine, prompt, **kw):
            prompts.append(prompt)
            return '[{"target_url": "/water/", "anchor_text": "water fountain"}]'

        result, _ = self._run(monkeypatch, [
            {"title": "Litter", "url": "/litter-box/"},
            {"title": "Water", "url": "/water/"},
        ], llm)
        assert [r["target_url"] for r in result] == ["/litter-box/", "/water/"]
        assert '"/water/"' in prompts[0]
        assert '"/litter-box/"' not in prompts[0]

    def test_near_miss_anchor_is_repaired_without_another_llm_call(self, monkeypatch):
        calls = []

        async def llm(engine, prompt, **kw):
            calls.append(prompt)
            return '[{"target_url": "/feeder/", "anchor_text": "Automatic Feeders"}]'

        result, fake = self._run(
            monkeypatch, [{"title": "Feeder", "url": "/feeder/"}], llm,
        )
        assert result == [{"anchor_text": "automatic feeder", "target_url": "/feeder/"}]
        assert len(calls) == 1
        assert fake._anchor_stats["repaired"] == 1
        assert fake._anchor_stats["retries"] == 0

    def test_retries_run_concurrently(self, monkeypatch):
        import time

        async def llm(engine, prompt, **kw):
            await asyncio.sleep(0.3)
            if "does not appear verbatim" in prompt:
                return "automatic feeder" if "/feeder/" in prompt else "water fountain"
            return (
                '[{"target_url": "/feeder/", "anchor_text": "self feeder"},'
                ' {"target_url": "/water/", "anchor_text": "drinking fountain"}]'
            )

        start = time.perf_counter()
        result, fake = self._run(monkeypatch, [
            {"title": "Feeder", "url": "/feeder/"},
            {"title": "Water", "url": "/water/"},
        ], llm)
        elapsed = time.perf_counter() - start

        assert {r["anchor_text"] for r in result} == {"automatic feeder", "water fountain"}
        assert fake._anchor_stats["retries"] == 2
        assert elapsed < 0.85  # 0.3 (anchors) + 0.3 (both retries), not 0.9


def test_o_key_runs_the_outgoing_pipeline_and_logs_timings(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    import hugin.log as log
    import hugin.tui.review as review
    from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
    from hugin.engines import Engine
    from hugin.tui.review import HuginScreen

    prompts = []

    async def fake_llm(engine, prompt, **kw):
        prompts.append(prompt)
        return "[]"

    monkeypatch.setattr(review, "call_llm", fake_llm)

    posts = _posts(tmp_path)
    posts[0].content = "word " * 700  # budget: 2 links
    site = MagicMock()
    site.post_url.return_value = "/x"
    site.warnings = []
    index = MagicMock()
    index.has_no_outgoing.return_value = False
    index.get_post_url.return_value = "/x"
    index.get_link_keywords.return_value = ""
    index.find_similar.return_value = [
        {"title": f"T{i}", "url": f"/t{i}/", "score": 0.8 - i / 100} for i in range(6)
    ]
    index.find_by_shared_tags.return_value = []
    index._cache = {}

    class Host(App):
        def on_mount(self):
            self.push_screen(HuginScreen(
                posts=posts, all_posts=list(posts),
                engine=Engine("t", "http://localhost/v1", "m", 30, None),
                pool={}, state={}, directory=tmp_path,
                config=HuginConfig(LinksConfig(), EmbeddingsConfig(), FrontmatterConfig()),
                site=site, index=index,
            ))

    async def go():
        app = Host()
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause(1.0)

    asyncio.run(go())
    # profile + anchors only: 6 candidates are few enough to skip the rerank
    assert len(prompts) == 2
    # budget 2 -> only 4 candidates reach the anchor step
    assert prompts[1].count('"url"') == 4
    text = log.LOG_PATH.read_text()
    assert "INFO outgoing p0.md" in text and "anchors" in text
    assert "NameError" not in text


def _count_outgoing_llm_calls(tmp_path, monkeypatch, rerank_min_posts):
    """LLM calls made by `o` for 7 posts and 14 candidates."""
    from unittest.mock import MagicMock

    import hugin.tui.review as review
    from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
    from hugin.engines import Engine
    from hugin.tui.review import HuginScreen

    prompts = []

    async def fake_llm(engine, prompt, **kw):
        prompts.append(prompt)
        return "[]"

    monkeypatch.setattr(review, "call_llm", fake_llm)

    posts = _posts(tmp_path)
    posts[0].content = "word " * 700
    site = MagicMock()
    site.post_url.return_value = "/x"
    site.warnings = []
    index = MagicMock()
    index.has_no_outgoing.return_value = False
    index.get_post_url.return_value = "/x"
    index.get_link_keywords.return_value = ""
    index.find_similar.return_value = [
        {"title": f"T{i}", "url": f"/t{i}/", "score": 0.9 - i / 100} for i in range(14)
    ]
    index.find_by_shared_tags.return_value = []
    index._cache = {}

    class Host(App):
        def on_mount(self):
            self.push_screen(HuginScreen(
                posts=posts, all_posts=list(posts),
                engine=Engine("t", "http://localhost/v1", "m", 30, None),
                pool={}, state={}, directory=tmp_path,
                config=HuginConfig(
                    LinksConfig(rerank_min_posts=rerank_min_posts),
                    EmbeddingsConfig(), FrontmatterConfig(),
                ),
                site=site, index=index,
            ))

    async def go():
        app = Host()
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause(1.0)

    asyncio.run(go())
    return prompts


def test_small_blog_skips_the_llm_rerank(tmp_path, monkeypatch):
    prompts = _count_outgoing_llm_calls(tmp_path, monkeypatch, rerank_min_posts=50)
    assert len(prompts) == 2  # profile + anchors


def test_big_blog_still_reranks(tmp_path, monkeypatch):
    prompts = _count_outgoing_llm_calls(tmp_path, monkeypatch, rerank_min_posts=5)
    assert len(prompts) == 3  # profile + rerank + anchors


def test_app_logs_error_notifications(tmp_path):
    from unittest.mock import MagicMock

    import hugin.log as log
    from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
    from hugin.engines import Engine
    from hugin.tui.app import HuginApp

    site = MagicMock()
    site.post_url.return_value = "/x"
    site.warnings = []
    index = MagicMock()
    index.has_no_outgoing.return_value = False
    index.get_post_url.return_value = "/x"
    index.get_link_keywords.return_value = ""
    index._cache = {}
    posts = _posts(tmp_path)
    app = HuginApp(
        posts=posts, all_posts=list(posts),
        engine=Engine("t", "http://localhost/v1", "m", 30, None),
        pool={}, state={}, directory=tmp_path,
        config=HuginConfig(LinksConfig(), EmbeddingsConfig(), FrontmatterConfig()),
        site=site, index=index,
    )

    async def go():
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            app.notify("Error: boom", severity="error")
            app.notify("just info")
            await pilot.pause()

    asyncio.run(go())
    text = log.LOG_PATH.read_text()
    assert "NOTIFICATION ERROR\nError: boom" in text
    assert "just info" not in text


def test_esc_cancels_a_running_outgoing_flow(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    import hugin.tui.review as review
    from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
    from hugin.engines import Engine
    from hugin.tui.review import STATE_BROWSING, HuginScreen, LoadingScreen

    started = []
    finished = []

    async def slow_llm(engine, prompt, **kw):
        started.append(prompt)
        await asyncio.sleep(30)
        finished.append(prompt)
        return "x"

    monkeypatch.setattr(review, "call_llm", slow_llm)

    posts = _posts(tmp_path)
    posts[0].content = "word " * 700
    site = MagicMock()
    site.post_url.return_value = "/x"
    site.warnings = []
    index = MagicMock()
    index.has_no_outgoing.return_value = False
    index.get_post_url.return_value = "/x"
    index.get_link_keywords.return_value = ""
    index._cache = {}
    seen = {}

    class Host(App):
        def on_mount(self):
            self.push_screen(HuginScreen(
                posts=posts, all_posts=list(posts),
                engine=Engine("t", "http://localhost/v1", "m", 30, None),
                pool={}, state={}, directory=tmp_path,
                config=HuginConfig(LinksConfig(), EmbeddingsConfig(), FrontmatterConfig()),
                site=site, index=index,
            ))

    async def go():
        app = Host()
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause(0.3)
            seen["during"] = isinstance(app.screen, LoadingScreen)
            await pilot.press("escape")
            await pilot.pause(0.3)
            seen["after"] = type(app.screen).__name__
            seen["state"] = app.screen._state

    asyncio.run(go())
    assert seen["during"] is True
    assert seen["after"] == "HuginScreen"
    assert seen["state"] == STATE_BROWSING
    assert len(started) == 1 and finished == []


class TestAnchorEdgeTrimming:
    def test_llm_anchor_loses_edge_prepositions_and_pure_stopword_anchors_are_dropped(
        self, monkeypatch,
    ):
        body = "Falamos do bolsonarismo e brincamos com o gato.\n"

        async def llm(engine, prompt, **kw):
            return (
                '[{"target_url": "/a/", "anchor_text": "do bolsonarismo"},'
                ' {"target_url": "/b/", "anchor_text": "com o"}]'
            )

        from types import SimpleNamespace

        import hugin.tui.review as review
        from hugin.config import LinksConfig
        from hugin.scanner import Post

        monkeypatch.setattr(review, "call_llm", llm)
        fake = SimpleNamespace(
            engine=None, config=SimpleNamespace(links=LinksConfig()), _anchor_stats={},
        )
        post = Post(Path("x.md"), {"title": "X"}, body, False)
        result = asyncio.run(review.HuginScreen._find_anchors(
            fake, post, [{"title": "A", "url": "/a/"}, {"title": "B", "url": "/b/"}], set(),
        ))
        assert result == [{"anchor_text": "bolsonarismo", "target_url": "/a/"}]


def _review_session(tmp_path, monkeypatch):
    """A HuginScreen host whose `o` ends in the REVIEWING state."""
    from unittest.mock import MagicMock

    import hugin.tui.review as review
    from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
    from hugin.engines import Engine
    from hugin.tui.review import HuginScreen

    async def fake_llm(engine, prompt, **kw):
        return "[]"

    monkeypatch.setattr(review, "call_llm", fake_llm)

    posts = _posts(tmp_path)
    for p in posts:
        p.content = "I love my litter box. " + "word " * 700
    site = MagicMock()
    site.post_url.return_value = "/x"
    site.warnings = []
    index = MagicMock()
    index.has_no_outgoing.return_value = False
    index.get_post_url.return_value = "/x"
    index.get_link_keywords.return_value = ""
    index.find_similar.return_value = [{"title": "Litter", "url": "/litter-box/", "score": 0.9}]
    index.find_by_shared_tags.return_value = []
    index._cache = {}

    class Host(App):
        def on_mount(self):
            self.push_screen(HuginScreen(
                posts=posts, all_posts=list(posts),
                engine=Engine("t", "http://localhost/v1", "m", 30, None),
                pool={}, state={}, directory=tmp_path,
                config=HuginConfig(LinksConfig(), EmbeddingsConfig(), FrontmatterConfig()),
                site=site, index=index,
            ))

    return Host()


def _current_after(tmp_path, monkeypatch, keys):
    from hugin.tui.review import STATE_REVIEWING

    out = {}

    async def go():
        app = _review_session(tmp_path, monkeypatch)
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause(0.8)
            out["reviewing"] = app.screen._state == STATE_REVIEWING
            await pilot.press(*keys)
            await pilot.pause(0.3)
            out["index"] = app.screen.current_index
            out["state"] = app.screen._state

    asyncio.run(go())
    return out


def test_navigating_away_from_open_suggestions_follows_the_cursor(tmp_path, monkeypatch):
    out = _current_after(tmp_path, monkeypatch, ["down", "down"])
    assert out["reviewing"] is True
    assert out["index"] == 2 and out["state"] == "browsing"


def test_esc_after_navigating_acts_on_the_post_under_the_cursor(tmp_path, monkeypatch):
    out = _current_after(tmp_path, monkeypatch, ["down", "escape"])
    assert out["index"] == 1 and out["state"] == "browsing"


def test_llm_single_weak_word_anchor_is_dropped(monkeypatch):
    from types import SimpleNamespace

    import hugin.tui.review as review
    from hugin.config import LinksConfig
    from hugin.scanner import Post

    async def llm(engine, prompt, **kw):
        return '[{"target_url": "/a/", "anchor_text": "quando"}]'

    monkeypatch.setattr(review, "call_llm", llm)
    fake = SimpleNamespace(
        engine=None, config=SimpleNamespace(links=LinksConfig()), _anchor_stats={},
    )
    post = Post(Path("x.md"), {"title": "X"}, "Lembro de quando era criança.\n", False)
    result = asyncio.run(review.HuginScreen._find_anchors(
        fake, post, [{"title": "A", "url": "/a/"}], set(),
    ))
    assert result == []


def test_wait_screen_shows_the_cancel_hint_and_esc_cancels(tmp_path, monkeypatch):
    finished = []

    async def slow_ask(message, persona, key):
        await asyncio.sleep(30)
        finished.append(True)
        return "T\n\nB"

    monkeypatch.setattr(echo_mod, "ask_echo", slow_ask)
    monkeypatch.setattr(echo_mod, "get_api_key", lambda: "k")
    monkeypatch.setattr(ed, "load_fulcrum_echo_persona", lambda: "x")
    hints = []

    async def keys(app, pilot):
        await pilot.pause(0.3)
        hints.append(str(app.screen.query_one("#echo-wait-hint").render()))
        await pilot.press("escape")

    result = _run(lambda: ed.EchoWaitScreen("q", _posts(tmp_path), tmp_path, None), keys)
    assert result is None
    assert hints == ["Esc to cancel"]
    assert finished == []
    assert len(list(tmp_path.glob("*.md"))) == 7  # no draft was written
