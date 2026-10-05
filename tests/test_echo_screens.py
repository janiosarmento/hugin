import asyncio
from datetime import datetime, timedelta

from textual.app import App

import hugin.tui.echo_draft as ed
from hugin.echo import EchoError
from hugin.scanner import Post


def _posts(tmp_path):
    out = []
    for i in range(6):
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


def test_prompt_screen_submits_text():
    async def keys(app, pilot):
        await pilot.press(*"hello", "ctrl+s")

    assert _run(ed.EchoPromptScreen, keys) == "hello"


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

    monkeypatch.setattr(ed, "ask_echo", fake_ask)
    monkeypatch.setattr(ed, "get_api_key", lambda: "k")
    monkeypatch.setattr(ed, "load_fulcrum_echo_persona", lambda: "Jane Doe")

    async def keys(app, pilot):
        await pilot.pause(0.3)

    path = _run(lambda: ed.EchoWaitScreen("write cats", _posts(tmp_path), tmp_path, None), keys)
    assert path.name == "echo-title.md"
    assert "Echo body." in path.read_text()
    assert seen["persona"] == "Jane Doe" and seen["message"].count("<post>") == 6
    assert seen["message"].endswith("write cats")


def test_wait_screen_error_returns_none(tmp_path, monkeypatch):
    def boom():
        raise EchoError("no key")

    monkeypatch.setattr(ed, "get_api_key", boom)
    monkeypatch.setattr(ed, "load_fulcrum_echo_persona", lambda: "x")

    async def keys(app, pilot):
        await pilot.pause(0.3)

    assert _run(lambda: ed.EchoWaitScreen("q", _posts(tmp_path), tmp_path, None), keys) is None
    assert len(list(tmp_path.glob("*.md"))) == 6


def test_h_key_creates_draft_in_main_screen(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from textual.widgets import DataTable

    from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
    from hugin.engines import Engine
    from hugin.tui.review import HuginScreen

    async def fake_ask(message, persona, key):
        return "From Echo\n\nText."

    monkeypatch.setattr(ed, "ask_echo", fake_ask)
    monkeypatch.setattr(ed, "get_api_key", lambda: "k")
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
            assert app.screen.query_one("#post-table", DataTable).row_count == 7

    asyncio.run(go())


def test_h_key_warns_without_enough_posts(tmp_path):
    from unittest.mock import MagicMock

    from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
    from hugin.engines import Engine
    from hugin.tui.review import HuginScreen

    posts = _posts(tmp_path)[:5]
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
        "        type: select\n        options: [Technology, Life]\n"
    )

    async def fake_ask(message, persona, key):
        return "Title\n\nBody"

    async def fake_llm(engine, prompt, system=None):
        return "Life"

    monkeypatch.setattr(ed, "ask_echo", fake_ask)
    monkeypatch.setattr(llm, "call_llm", fake_llm)
    monkeypatch.setattr(ed, "get_api_key", lambda: "k")
    monkeypatch.setattr(ed, "load_fulcrum_echo_persona", lambda: "x")

    async def keys(app, pilot):
        await pilot.pause(0.3)

    path = _run(lambda: ed.EchoWaitScreen("q", _posts(tmp_path), tmp_path, object()), keys)
    text = path.read_text()
    assert "- Life" in text and "TBD" not in text.split("description")[0].split("categories:")[1]
