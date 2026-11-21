import asyncio
import json
from datetime import datetime, timedelta

import httpx
from textual.app import App

from hugin import drafting
from hugin import factcheck as fc
from hugin import research as web
from hugin import verify
import hugin.tui.draft_screens as ed
from hugin.engines import Engine
from hugin.scanner import Post

ENGINE = Engine("t", "http://localhost/v1", "m", 30, None)


def _research():
    found = web.Research("cat sleep", "English", "US", "en", datetime(2026, 11, 11))
    found.results = [
        web.SearchResult(1, "Vet", "https://vet.example/sleep", "cats sleep 15 hours"),
        web.SearchResult(2, "Blog", "https://blog.example/cats", "snippet two"),
    ]
    found.pages = {
        "https://vet.example/sleep": web.PageRead("https://vet.example/sleep", text="Cats sleep 12 to 16 hours."),
        "https://blog.example/cats": web.PageRead("https://blog.example/cats", error="HTTP 403"),
    }
    return found


# --- links ------------------------------------------------------------------


def test_unknown_links_flags_urls_from_nowhere():
    draft = (
        "See [the vet](https://www.vet.example/sleep/) and [a study](https://journal.example/x), "
        "[treats](https://amzn.to/abc), [home](/other-post/) ![pic](https://img.example/a.png)."
    )
    allowed = verify.allowed_links("Buy [treats](https://amzn.to/abc).", _research(), None)
    # Same URL with www, a trailing slash or another scheme is the same link
    assert verify.unknown_links(draft, allowed) == [
        ("unknown link", "https://journal.example/x  “a study”"),
    ]


def test_allowed_links_include_the_fact_check_sources():
    check = fc.FactCheck(_research(), [])
    assert "https://blog.example/cats" in verify.allowed_links(None, None, check)


def test_linked_sources_use_the_page_text_or_say_it_is_a_snippet():
    draft = "[a](https://vet.example/sleep) [b](https://blog.example/cats)"
    sources = verify.linked_sources(draft, _research())
    assert sources[0] == (1, "Vet", "https://vet.example/sleep", "Cats sleep 12 to 16 hours.")
    assert sources[1][3] == "(search snippet only) snippet two"
    assert verify.linked_sources("[a](https://vet.example/sleep)", None) == []


# --- statements -------------------------------------------------------------


def test_verify_prompt_carries_every_reference():
    prompt = verify.build_verify_prompt(
        "Draft text.", original="Old text.", decisions='1. KEEP: "x" [1]',
        sources=[(1, "Vet", "https://vet.example/sleep", "Cats sleep 12 to 16 hours.")],
    )
    assert "<original>\nOld text.\n</original>" in prompt
    assert '<decisions>\n1. KEEP: "x" [1]\n</decisions>' in prompt
    assert '<source n="1" url="https://vet.example/sleep">' in prompt
    assert prompt.rstrip().endswith('[{"kind": "unsupported", "quote": "...", "note": "..."}]')
    assert "<brief>" not in prompt


def test_parse_issues_labels_kinds_and_skips_empty_quotes():
    answer = json.dumps([
        {"kind": "unsupported", "quote": "Cats  sleep 20 hours", "note": "No reference says 20."},
        {"kind": "attribution", "quote": "says the vet", "note": ""},
        {"kind": "odd", "quote": "x"},
        {"kind": "decision", "quote": ""},
    ])
    assert verify.parse_issues(answer) == [
        ("unsupported claim", "“Cats sleep 20 hours” — No reference says 20."),
        ("wrong attribution", "“says the vet”"),
        ("unsupported claim", "“x”"),
    ]
    assert verify.parse_issues("[]") == []


def test_a_failed_check_says_it_was_not_done(monkeypatch):
    async def down(engine, prompt, system=None):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(verify, "call_llm", down)
    issues = asyncio.run(verify.check_statements(ENGINE, "draft", original="old"))
    assert issues == [("verification", "not done: t failed (down); check the facts yourself")]


# --- fixing -----------------------------------------------------------------


def test_strip_links_keeps_allowed_and_internal_links():
    draft = ("[a](https://vet.example/sleep/) [b](https://x.example) [c](/cats/) "
             "![img](https://x.example/i.png)")
    assert verify.strip_links(draft, {"https://vet.example/sleep"}) == (
        "[a](https://vet.example/sleep/) b [c](/cats/) ![img](https://x.example/i.png)"
    )


def test_apply_fixes_replaces_drops_and_keeps_unmatched_open():
    issues = [verify.Issue("unsupported claim", "20 hours"),
              verify.Issue("wrong attribution", "says the vet"),
              verify.Issue("unsupported claim", "25 Hz")]
    draft = "Cats sleep 20 hours. They purr, says the vet.\n\nCats purr at 25 Hz."
    answer = json.dumps([
        {"n": [1], "find": "Cats sleep 20 hours.", "replace": "Cats sleep a lot."},
        {"n": 2, "find": "They purr, says the vet.", "replace": "", "note": "Dropped."},
        {"n": [3], "find": "Cats purr at 25Hz.", "replace": "x"},  # not copied exactly
        {"n": [9], "find": "Cats", "replace": "Dogs"},  # no such problem
    ])
    fixed, rows, still = verify.apply_fixes(draft, issues, answer)
    assert fixed == "Cats sleep a lot.\n\nCats purr at 25 Hz."
    assert rows == [
        ("fixed", "“Cats sleep 20 hours.” → “Cats sleep a lot.”"),
        ("removed", "“They purr, says the vet.” — Dropped."),
    ]
    assert still == [issues[2]]


def test_a_failed_fix_leaves_every_problem_open(monkeypatch):
    async def llm(engine, prompt, system=None):
        if system == verify.FIX_SYSTEM_PROMPT:
            raise httpx.ConnectError("down")
        return json.dumps([{"kind": "unsupported", "quote": "20 hours"}])

    monkeypatch.setattr(verify, "call_llm", llm)
    draft, fixes, left = asyncio.run(verify.check_and_fix(ENGINE, "Cats sleep 20 hours.", original="old"))
    assert draft == "Cats sleep 20 hours." and fixes == []
    assert left == [("unsupported claim", "“20 hours”")]


def test_nothing_found_means_no_fix_call(monkeypatch):
    calls = []

    async def llm(engine, prompt, system=None):
        calls.append(system)
        return "[]"

    monkeypatch.setattr(verify, "call_llm", llm)
    assert asyncio.run(verify.check_and_fix(ENGINE, "draft", original="old")) == ("draft", [], [])
    assert calls == [verify.VERIFY_SYSTEM_PROMPT]


# --- wait screen ------------------------------------------------------------


def _posts(tmp_path):
    out = []
    for i in range(7):
        path = tmp_path / f"p{i}.md"
        path.write_text("body")
        out.append(Post(path, {"title": f"P{i}"}, "body", False,
                        date=datetime.now() - timedelta(days=i + 1), mtime=path.stat().st_mtime))
    return out


def _run(screen_factory, keys_fn):
    result = {}

    async def go():
        app = App()
        async with app.run_test(size=(140, 50)) as pilot:
            app.push_screen(screen_factory(), lambda r: result.setdefault("v", r))
            await pilot.pause()
            await keys_fn(app, pilot)
            await pilot.pause(0.3)

    asyncio.run(go())
    return result.get("v", "unset")


def test_researched_draft_is_fixed_before_saving(tmp_path, monkeypatch):
    prompts = []

    async def writer(engine, message, system=None):
        return ("Cat Sleep\n\nCats sleep 20 hours, says [the vet](https://vet.example/sleep) "
                "and [a study](https://journal.example/x).")

    async def checker(engine, prompt, system=None):
        prompts.append(prompt)
        if system == verify.FIX_SYSTEM_PROMPT:
            return json.dumps([{"n": [1], "find": "Cats sleep 20 hours,",
                                "replace": "Cats sleep 12 to 16 hours,", "note": "Per the vet."}])
        return json.dumps([{"kind": "unsupported", "quote": "Cats sleep 20 hours",
                            "note": "The vet says 12 to 16."}])

    monkeypatch.setattr(drafting, "call_llm", writer)
    monkeypatch.setattr(verify, "call_llm", checker)
    monkeypatch.setattr(web, "RESEARCH_DIR", tmp_path / "research")
    posts_dir = tmp_path / "posts"
    posts_dir.mkdir()

    async def keys(app, pilot):
        await pilot.pause(0.3)
        assert not isinstance(app.screen, ed.DraftCheckScreen)  # nothing left to show

    path = _run(lambda: ed.DraftWaitScreen(
        "write cats", _posts(posts_dir), posts_dir, ENGINE,
        research=_research(), brief="## Facts\n- 12 to 16 hours [1]",
    ), keys)
    assert path.name == "cat-sleep.md"
    saved = path.read_text()
    assert "Cats sleep 12 to 16 hours, says [the vet](https://vet.example/sleep)" in saved
    assert "and a study." in saved and "journal.example" not in saved
    # The checker sees the brief and the text of the source the draft links
    assert "<brief>\n## Facts" in prompts[0]
    assert "Cats sleep 12 to 16 hours." in prompts[0]
    assert "<original>" not in prompts[0]
    assert "<problems>\n1. unsupported claim: “Cats sleep 20 hours”" in prompts[1]
    log = (tmp_path / "research" / "cat-sleep.md").read_text()
    assert "## Fixed after writing" in log and "journal.example" in log


def test_what_cannot_be_fixed_is_shown_with_what_was(tmp_path, monkeypatch):
    async def writer(engine, message, system=None):
        return "Cat Sleep\n\nCats sleep 20 hours. Cats purr at 25 Hz."

    async def checker(engine, prompt, system=None):
        if system == verify.FIX_SYSTEM_PROMPT:
            return json.dumps([
                {"n": 1, "find": "Cats sleep 20 hours.", "replace": "Cats sleep 12 to 16 hours."},
                {"n": 2, "find": "not in the draft", "replace": "x"},
            ])
        return json.dumps([{"kind": "unsupported", "quote": "20 hours"},
                           {"kind": "unsupported", "quote": "25 Hz", "note": "No source."}])

    monkeypatch.setattr(drafting, "call_llm", writer)
    monkeypatch.setattr(verify, "call_llm", checker)
    monkeypatch.setattr(web, "RESEARCH_DIR", tmp_path / "research")
    posts_dir = tmp_path / "posts"
    posts_dir.mkdir()

    async def keys(app, pilot):
        await pilot.pause(0.3)
        assert isinstance(app.screen, ed.DraftCheckScreen)
        shown = str(app.screen.query_one("#check-list Static").render())
        left, done = shown.split("Already fixed")
        assert "25 Hz" in left and "20 hours" not in left
        assert "12 to 16 hours" in done
        await pilot.press("ctrl+s")

    path = _run(lambda: ed.DraftWaitScreen(
        "write cats", _posts(posts_dir), posts_dir, ENGINE,
        research=_research(), brief="## Facts\n- 12 to 16 hours [1]",
    ), keys)
    assert "Cats sleep 12 to 16 hours. Cats purr at 25 Hz." in path.read_text()


def test_plain_new_draft_skips_the_statement_check(tmp_path, monkeypatch):
    async def writer(engine, message, system=None):
        return "Cat Title\n\nCat body."

    async def checker(engine, prompt, system=None):
        raise AssertionError("no references: no statement check")

    monkeypatch.setattr(drafting, "call_llm", writer)
    monkeypatch.setattr(verify, "call_llm", checker)

    async def keys(app, pilot):
        await pilot.pause(0.3)

    path = _run(lambda: ed.DraftWaitScreen("write cats", _posts(tmp_path), tmp_path, ENGINE), keys)
    assert path.name == "cat-title.md"


def test_plain_new_draft_drops_invented_links(tmp_path, monkeypatch):
    async def writer(engine, message, system=None):
        return "Cat Title\n\nSee [this](https://made-up.example/page)."

    monkeypatch.setattr(drafting, "call_llm", writer)

    async def keys(app, pilot):
        await pilot.pause(0.3)
        assert not isinstance(app.screen, ed.DraftCheckScreen)

    path = _run(lambda: ed.DraftWaitScreen("write cats", _posts(tmp_path), tmp_path, ENGINE), keys)
    assert "See this." in path.read_text() and "made-up.example" not in path.read_text()


def test_refactor_is_checked_against_the_original_and_the_decisions(tmp_path, monkeypatch):
    seen = {}

    async def writer(engine, message, system=None):
        return "New Title\n\nCats get HCM."

    async def checker(engine, prompt, system=None):
        seen["prompt"] = prompt
        return "[]"

    monkeypatch.setattr(drafting, "call_llm", writer)
    monkeypatch.setattr(verify, "call_llm", checker)
    monkeypatch.setattr(web, "RESEARCH_DIR", tmp_path / "research")
    posts_dir = tmp_path / "posts"
    posts_dir.mkdir()
    original = Post(posts_dir / "old.md", {"title": "Old"}, "Cats get HCM.", False)
    check = fc.FactCheck(_research(), [fc.Claim(1, "Cats get HCM.", "hcm", verdict="keep", cited=[1])])

    async def keys(app, pilot):
        await pilot.pause(0.3)

    path = _run(lambda: ed.DraftWaitScreen(
        "update", _posts(posts_dir), posts_dir, ENGINE, original=original, fact_check=check,
    ), keys)
    assert path.name == "new-title.md"
    assert "<original>\nCats get HCM.\n</original>" in seen["prompt"]
    assert '<decisions>\n1. KEEP: "Cats get HCM." [1]' in seen["prompt"]
