import asyncio
import json
import re
from datetime import datetime, timedelta

import pytest
from textual.app import App
from textual.widgets import Checkbox, Input, Select

from hugin import drafting
from hugin import factcheck as fc
from hugin import research as web
import hugin.tui.draft_screens as ed
from hugin.engines import Engine
from hugin.scanner import Post

ENGINE = Engine("t", "http://localhost/v1", "m", 30, None)
BRAVE = r"https://api\.search\.brave\.com/.*[?&]q={}(&|$).*"

HEALTH = "<p>" + "British Shorthairs are among the breeds with more hypertrophic cardiomyopathy. " * 6 + "</p>"
PAGE = f"<html><body><article><h1>HCM</h1>{HEALTH}</article></body></html>"


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(fc, "SEARCH_INTERVAL", 0)


def _brave(*urls):
    return {"web": {"results": [
        {"title": f"T {u}", "url": u, "description": f"snippet {u}"} for u in urls
    ]}}


def _claims():
    return [
        fc.Claim(1, "British Shorthairs can have HCM.", "hcm", english=True),
        fc.Claim(2, "The breed was shown in 1871.", "history"),
    ]


def _check():
    research = web.Research("fact check", "Portuguese", "BR", "pt-br", datetime(2026, 11, 11))
    research.results = [
        web.SearchResult(1, "Cornell", "https://vet.example/hcm", "hcm snippet"),
        web.SearchResult(2, "GCCF", "https://gccf.example/british", "1871 snippet"),
    ]
    research.pages = {
        "https://vet.example/hcm": web.PageRead("https://vet.example/hcm", text="HCM text"),
        "https://gccf.example/british": web.PageRead("https://gccf.example/british", error="HTTP 403"),
    }
    claims = _claims()
    claims[0].sources = [1]
    claims[1].sources = [2]
    return fc.FactCheck(research, claims)


# --- claims -----------------------------------------------------------------


def test_parse_claims_reads_a_fenced_array_and_skips_junk():
    answer = "```json\n" + json.dumps([
        {"claim": "Cats  sleep 15 hours.", "query": "cat sleep hours", "english": True},
        {"claim": "", "query": "x"},
        "not an object",
        {"claim": "Café cats exist.", "english": "yes"},
    ]) + "\n```"
    claims = fc.parse_claims(answer)
    assert [(c.n, c.text, c.english) for c in claims] == [
        (1, "Cats sleep 15 hours.", True), (2, "Café cats exist.", False),
    ]
    assert claims[1].query == "Café cats exist."  # no query: the claim's words
    with pytest.raises(ValueError):
        fc.parse_claims("no claims here")


def test_parse_claims_stops_at_the_limit():
    answer = json.dumps([{"claim": f"c{i}", "query": "q"} for i in range(20)])
    assert len(fc.parse_claims(answer, limit=5)) == 5


def test_relevant_excerpt_keeps_the_paragraphs_about_the_claim():
    text = "\n".join(["Unrelated paragraph about food and toys."] * 40
                     + ["Hypertrophic cardiomyopathy is common in British Shorthairs."]
                     + ["More unrelated text on grooming."] * 40)
    excerpt = fc.relevant_excerpt(text, "British Shorthair hypertrophic cardiomyopathy", limit=300)
    assert "cardiomyopathy" in excerpt
    assert len(excerpt) <= 300


# --- search -----------------------------------------------------------------


def test_gather_searches_each_claim_and_numbers_sources_once(httpx_mock):
    httpx_mock.add_response(url=re.compile(BRAVE.format("hcm")),
                            json=_brave("https://vet.example/hcm", "https://shared.example/p"))
    httpx_mock.add_response(url=re.compile(BRAVE.format("history")),
                            json=_brave("https://shared.example/p", "https://gccf.example/british"))
    httpx_mock.add_response(url="https://vet.example/hcm", html=PAGE)
    httpx_mock.add_response(url="https://shared.example/p", html=PAGE)
    httpx_mock.add_response(url="https://gccf.example/british", status_code=403)
    steps = []

    check = asyncio.run(fc.gather(_claims(), "key", "Portuguese",
                                  progress=lambda d, t: steps.append((d, t))))

    searches = [r for r in httpx_mock.get_requests() if "brave" in r.url.host]
    # The English claim is searched in English, the other in the blog's language
    assert searches[0].url.params["search_lang"] == "en"
    assert searches[1].url.params["search_lang"] == "pt-br"
    assert [r.url for r in check.research.results] == [
        "https://vet.example/hcm", "https://shared.example/p", "https://gccf.example/british",
    ]
    assert check.claims[0].sources == [1, 2]
    assert check.claims[1].sources == [2, 3]  # read pages first
    assert check.research.pages_read == 2
    assert steps == [(1, 2), (2, 2)]


def test_a_failed_search_leaves_that_claim_unconfirmed(httpx_mock):
    httpx_mock.add_response(url=re.compile(BRAVE.format("hcm")), status_code=500)
    httpx_mock.add_response(url=re.compile(BRAVE.format("history")),
                            json=_brave("https://gccf.example/british"))
    httpx_mock.add_response(url="https://gccf.example/british", html=PAGE)

    check = asyncio.run(fc.gather(_claims(), "key", "Portuguese"))
    assert check.claims[0].error == "search failed: HTTP 500"
    assert check.claims[0].sources == []
    assert check.claims[1].sources == [1]


def test_a_rate_limited_search_is_retried_once(httpx_mock):
    claims = _claims()[:1]
    httpx_mock.add_response(url=re.compile(BRAVE.format("hcm")), status_code=429)
    httpx_mock.add_response(url=re.compile(BRAVE.format("hcm")),
                            json=_brave("https://vet.example/hcm"))
    httpx_mock.add_response(url="https://vet.example/hcm", html=PAGE)

    check = asyncio.run(fc.gather(claims, "key", "English"))
    assert check.claims[0].sources == [1]


def test_gather_fails_when_every_search_fails(httpx_mock):
    httpx_mock.add_response(url=re.compile(BRAVE.format("hcm")), status_code=401)
    httpx_mock.add_response(url=re.compile(BRAVE.format("history")), status_code=401)
    with pytest.raises(web.ResearchError, match="every claim"):
        asyncio.run(fc.gather(_claims(), "key", "English"))
    with pytest.raises(web.ResearchError, match="No Brave"):
        asyncio.run(fc.gather(_claims(), None, "English"))


# --- verdicts and writer ----------------------------------------------------


def test_verdict_prompt_separates_read_pages_from_snippets():
    prompt = fc.build_verdict_prompt(_check(), "Old post")
    assert '<claim n="1">\nBritish Shorthairs can have HCM.\n</claim>' in prompt
    assert '<source n="1" status="read" url="https://vet.example/hcm">' in prompt
    assert "HCM text" in prompt
    assert '<source n="2" status="snippet" url="https://gccf.example/british">' in prompt
    assert '"Old post"' in prompt


def test_apply_verdicts_does_not_trust_a_bad_answer():
    check = _check()
    fc.apply_verdicts(check, json.dumps([
        {"n": 1, "verdict": "correct", "correction": "", "cited": [1]},  # no correction
        {"n": 2, "verdict": "keep", "cited": [1, 2, 9], "note": "GCCF says so."},
    ]))
    assert check.claims[0].verdict == "unconfirmed"
    assert check.claims[1].verdict == "keep"
    assert check.claims[1].cited == [2]  # only its own sources count
    assert check.claims[1].note == "GCCF says so."

    check = _check()
    fc.apply_verdicts(check, json.dumps([{"n": 1, "verdict": "maybe"}]))
    assert check.claims[0].verdict == "unconfirmed"
    assert check.claims[1].note == "no verdict returned"


def test_writer_context_lists_decisions_and_cited_sources():
    check = _check()
    check.claims[0].verdict, check.claims[0].cited = "keep", [1]
    check.claims[1].verdict, check.claims[1].correction = "correct", "Shown at Crystal Palace in 1871."
    check.claims[1].cited = [2]
    context = fc.writer_context(check)
    assert '1. KEEP: "British Shorthairs can have HCM." [1]' in context
    assert '2. CORRECT: "The breed was shown in 1871." -> "Shown at Crystal Palace in 1871." [2]' in context
    assert "[1] Cornell — https://vet.example/hcm" in context
    check.claims[0].cited = check.claims[1].cited = []
    assert "Sources" not in fc.writer_context(check)


def test_build_message_applies_the_fact_check(tmp_path):
    original = Post(tmp_path / "old.md", {"title": "Old"}, "Cats get HCM.", False)
    message = drafting.build_message([], "update it", original, claims='1. KEEP: "x"')
    assert '<fact-check>\n1. KEEP: "x"\n</fact-check>' in message
    assert "UNCONFIRMED: keep it as the original says" in message
    assert "names that source in the sentence" in message
    assert "<research>" not in message


def test_save_log(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "RESEARCH_DIR", tmp_path / "research")
    check = _check()
    check.claims[0].verdict, check.claims[0].cited, check.claims[0].note = "keep", [1], "Cornell lists it."
    path = fc.save_log(check, "update it", tmp_path / "new-post.md")
    text = path.read_text()
    assert "1. **keep**: British Shorthairs can have HCM." in text
    assert "Cornell lists it." in text
    assert "query: hcm (English)" in text
    assert "https://gccf.example/british (snippet only (read failed: HTTP 403))" in text


# --- screens ----------------------------------------------------------------


def _run(screen_factory, keys_fn, size=(140, 50)):
    result = {}

    async def go():
        app = App()
        async with app.run_test(size=size) as pilot:
            app.push_screen(screen_factory(), lambda r: result.setdefault("v", r))
            await pilot.pause()
            await keys_fn(app, pilot)
            await pilot.pause(0.3)

    asyncio.run(go())
    return result.get("v", "unset")


def _posts(tmp_path):
    out = []
    for i in range(7):
        path = tmp_path / f"p{i}.md"
        path.write_text("body")
        out.append(Post(path, {"title": f"P{i}"}, "body", False,
                        date=datetime.now() - timedelta(days=i + 1), mtime=path.stat().st_mtime))
    return out


def _fake_pipeline(monkeypatch, seen):
    async def list_claims(engine, title, body):
        seen["title"] = title
        return _claims()

    async def gather(claims, api_key, language, progress=None):
        seen["language"] = language
        check = _check()
        check.claims = claims
        claims[0].sources, claims[1].sources = [1], [2]
        progress(2, 2)
        return check

    async def weigh(check, engine, title):
        check.claims[0].verdict, check.claims[0].cited = "keep", [1]
        check.claims[1].verdict = "unconfirmed"

    monkeypatch.setattr(fc, "list_claims", list_claims)
    monkeypatch.setattr(fc, "gather", gather)
    monkeypatch.setattr(fc, "weigh", weigh)


def test_fact_check_screen_runs_the_pipeline(tmp_path, monkeypatch):
    seen = {}
    _fake_pipeline(monkeypatch, seen)
    original = Post(tmp_path / "old.md", {"title": "Old"}, "Cats get HCM.", False)

    async def keys(app, pilot):
        await pilot.pause(0.3)

    check, error = _run(lambda: ed.FactCheckScreen(original, ENGINE, "key", "English"), keys)
    assert error == ""
    assert [c.verdict for c in check.claims] == ["keep", "unconfirmed"]
    assert seen["title"] == "Old"


def test_fact_check_screen_reports_failures(tmp_path, monkeypatch):
    async def broken(engine, title, body):
        raise ValueError("no JSON array in the answer")

    monkeypatch.setattr(fc, "list_claims", broken)
    original = Post(tmp_path / "old.md", {"title": "Old"}, "x", False)

    async def keys(app, pilot):
        await pilot.pause(0.3)

    check, error = _run(lambda: ed.FactCheckScreen(original, ENGINE, "key", "English"), keys)
    assert check is None
    assert "failed listing the claims" in error
    assert _run(lambda: ed.FactCheckScreen(original, ENGINE, None, "English"), keys) == (
        None, f"No Brave Search key in the vault ({web.SEARCH_SECRET})",
    )


def test_claims_review_applies_the_edits():
    async def keys(app, pilot):
        app.screen.query_one("#verdict-2", Select).value = "correct"
        await pilot.pause()
        await pilot.press("ctrl+s")  # a correction needs its text
        assert isinstance(app.screen, ed.ClaimsReviewScreen)
        app.screen.query_one("#correction-2", Input).value = "Shown at Crystal Palace in 1871."
        app.screen.query_one("#verdict-1", Select).value = "remove"
        await pilot.press("ctrl+s")

    check = _run(lambda: ed.ClaimsReviewScreen(_check()), keys)
    assert [(c.verdict, c.correction) for c in check.claims] == [
        ("remove", ""), ("correct", "Shown at Crystal Palace in 1871."),
    ]


def test_claims_review_after_failure_offers_writing_without_it():
    async def keys(app, pilot):
        await pilot.click("#btn-brief-skip")

    assert _run(lambda: ed.ClaimsReviewScreen(None, "HTTP 429"), keys) == ed.WITHOUT_RESEARCH


def _R_flow(tmp_path, monkeypatch, review):
    from unittest.mock import MagicMock

    from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
    from hugin.tui.review import HuginScreen

    seen = {}
    _fake_pipeline(monkeypatch, seen)

    async def fake_writer(engine, message, system=None):
        seen["message"] = message
        return "Rewritten\n\nbody"

    monkeypatch.setattr(drafting, "call_llm", fake_writer)
    monkeypatch.setattr(web, "search_api_key", lambda: "key")
    monkeypatch.setattr(web, "RESEARCH_DIR", tmp_path / "research")
    posts_dir = tmp_path / "posts"
    posts_dir.mkdir()
    posts = _posts(posts_dir)
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
                posts=posts, all_posts=list(posts), engine=ENGINE,
                pool={}, state={}, directory=posts_dir,
                config=HuginConfig(LinksConfig(), EmbeddingsConfig(), FrontmatterConfig()),
                site=site, index=index,
            ))

    async def go():
        app = Host()
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await pilot.press("R")
            await pilot.pause()
            assert not app.screen.query("#draft-query")  # a refactor needs no query
            await pilot.press(*"update")
            assert app.screen.query_one("#draft-review", Checkbox).disabled
            app.screen.query_one("#draft-research", Checkbox).value = True
            await pilot.pause()
            app.screen.query_one("#draft-review", Checkbox).value = review
            await pilot.press("ctrl+s")
            await pilot.pause(0.5)
            if review:
                assert isinstance(app.screen, ed.ClaimsReviewScreen)
                await pilot.press("ctrl+s")
                await pilot.pause(0.5)
            assert not isinstance(app.screen, ed.ClaimsReviewScreen)
            assert (posts_dir / "rewritten.md").exists()

    asyncio.run(go())
    assert seen["title"] == "P0"
    assert '<fact-check>\n1. KEEP: "British Shorthairs can have HCM." [1]' in seen["message"]
    assert "<research>" not in seen["message"]
    assert "1. **keep**" in (tmp_path / "research" / "rewritten.md").read_text()


def test_R_with_fact_check_writes_without_asking(tmp_path, monkeypatch):
    _R_flow(tmp_path, monkeypatch, review=False)


def test_R_with_fact_check_can_show_the_decisions_first(tmp_path, monkeypatch):
    _R_flow(tmp_path, monkeypatch, review=True)


def test_cached_claims_skip_the_wait_between_searches(httpx_mock, monkeypatch):
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(fc, "SEARCH_INTERVAL", 1.1)
    monkeypatch.setattr(fc.asyncio, "sleep", sleep)
    httpx_mock.add_response(url=re.compile(BRAVE.format("hcm")), json=_brave("https://vet.example/hcm"))
    httpx_mock.add_response(url=re.compile(BRAVE.format("history")), json=_brave("https://gccf.example/b"))
    httpx_mock.add_response(url="https://vet.example/hcm", html=PAGE, is_reusable=True)
    httpx_mock.add_response(url="https://gccf.example/b", html=PAGE, is_reusable=True)

    asyncio.run(fc.gather(_claims(), "key", "Portuguese"))
    assert waits == [1.1]  # between the two real searches
    waits.clear()
    asyncio.run(fc.gather(_claims(), "key", "Portuguese"))
    assert waits == []  # both from the cache: no wait, no request
    assert sum("brave" in r.url.host for r in httpx_mock.get_requests()) == 2
