import asyncio
import re
from datetime import datetime, timedelta

import frontmatter
import httpx
import pytest
from textual.app import App
from textual.widgets import Button, Checkbox, Input, TextArea

from hugin import drafting
from hugin import prompts
from hugin import research as web
import hugin.tui.draft_screens as ed
from hugin.engines import Engine
from hugin.scanner import Post

ENGINE = Engine("t", "http://localhost/v1", "m", 30, None)

ARTICLE = "<p>" + "Cats sleep about fifteen hours a day, more in winter. " * 10 + "</p>"
PAGE = (
    "<html><head><style>.x{}</style><script>var junk = 1;</script></head><body>"
    "<nav>Home | About | Contact</nav>"
    f"<article><h1>Cat sleep</h1>{ARTICLE}</article>"
    "<footer>Copyright</footer></body></html>"
)


def _brave(n=5):
    return {"web": {"results": [
        {"title": f"<strong>Result</strong> {i}", "url": f"https://site{i}.example/page",
         "description": f"Snippet &amp; text {i}"}
        for i in range(1, n + 1)
    ]}}


def _research(n=3):
    found = web.Research("cat sleep", "English", "US", "en", datetime(2026, 11, 11, 10, 0))
    found.results = [
        web.SearchResult(i, f"Result {i}", f"https://site{i}.example/page", f"snippet {i}")
        for i in range(1, n + 1)
    ]
    found.pages = {
        "https://site1.example/page": web.PageRead("https://site1.example/page", text="Full text one."),
        "https://site2.example/page": web.PageRead("https://site2.example/page", error="HTTP 403"),
    }
    return found


# --- search and reading -----------------------------------------------------


def test_parse_brave_cleans_markup_and_skips_bad_urls():
    data = _brave(3)
    data["web"]["results"].insert(1, {"title": "No scheme", "url": "ftp://x"})
    results = web.parse_brave(data)
    assert [r.rank for r in results] == [1, 2, 3]
    assert results[0].title == "Result 1"
    assert results[0].snippet == "Snippet & text 1"
    assert web.parse_brave({}) == []


def test_html_to_text_keeps_the_article_and_drops_chrome():
    text = web.html_to_text(PAGE)
    assert text.startswith("# Cat sleep")
    assert "fifteen hours" in text
    for junk in ("var junk", "Home | About", "Copyright", ".x{}"):
        assert junk not in text


def test_html_to_text_falls_back_to_the_whole_body():
    body = "<p>" + "Plain café text without an article tag. " * 20 + "</p>"
    assert "café" in web.html_to_text(f"<html><body><nav>menu</nav>{body}</body></html>")


def test_gather_reads_up_to_the_page_limit(httpx_mock):
    httpx_mock.add_response(url=re.compile(r"https://api\.search\.brave\.com/.*"), json=_brave())
    httpx_mock.add_response(url="https://site1.example/page", html=PAGE)
    httpx_mock.add_response(url="https://site2.example/page", status_code=403)
    httpx_mock.add_response(url="https://site3.example/page", html="<html><body>tiny</body></html>")
    httpx_mock.add_response(url="https://site4.example/page", html=PAGE)
    httpx_mock.add_response(url="https://site5.example/page", html=PAGE)

    found = asyncio.run(web.gather("cat sleep", "key", "Portuguese"))

    request = httpx_mock.get_requests()[0]
    assert request.headers["X-Subscription-Token"] == "key"
    assert request.url.params["country"] == "BR"
    assert request.url.params["search_lang"] == "pt-br"
    assert request.url.params["q"] == "cat sleep"
    page_request = next(r for r in httpx_mock.get_requests() if "site1" in r.url.host)
    assert "text/html" in page_request.headers["Accept"]
    status = [found.status(r) for r in found.results]
    assert status == [
        "read",
        "snippet only (read failed: HTTP 403)",
        "snippet only (read failed: no readable text)",
        "read",
        "read",
    ]
    assert found.pages_read == 3


def test_gather_marks_pages_past_the_limit_as_skipped(httpx_mock, monkeypatch):
    monkeypatch.setattr(web, "N_PAGES", 2)
    httpx_mock.add_response(url=re.compile(r"https://api\.search\.brave\.com/.*"), json=_brave(3))
    for i in (1, 2, 3):
        httpx_mock.add_response(url=f"https://site{i}.example/page", html=PAGE)

    found = asyncio.run(web.gather("cat sleep", "key", "English"))
    assert found.pages_read == 2
    assert found.status(found.results[2]) == "snippet only (over the page limit)"


def test_gather_reports_search_failures(httpx_mock):
    with pytest.raises(web.ResearchError, match="no.*key|No Brave"):
        asyncio.run(web.gather("q", None, "English"))

    httpx_mock.add_response(url=re.compile(r"https://api\.search\.brave\.com/.*"), status_code=429)
    with pytest.raises(web.ResearchError, match="HTTP 429"):
        asyncio.run(web.gather("q", "key", "English"))


def test_gather_without_results(httpx_mock):
    httpx_mock.add_response(url=re.compile(r"https://api\.search\.brave\.com/.*"), json={"web": {"results": []}})
    with pytest.raises(web.ResearchError, match="No search results"):
        asyncio.run(web.gather("q", "key", "English"))


def test_non_html_pages_are_not_read(httpx_mock):
    httpx_mock.add_response(url="https://x.example/a.pdf", content=b"%PDF", headers={"content-type": "application/pdf"})

    async def go():
        async with httpx.AsyncClient() as client:
            return await web.fetch_page(client, "https://x.example/a.pdf")

    page = asyncio.run(go())
    assert not page.ok
    assert page.error == "not a web page (application/pdf)"


# --- query and brief --------------------------------------------------------


def test_parse_query_strips_labels_and_quotes():
    assert web.parse_query('Query: "cat sleep hours"\n', "fb") == "cat sleep hours"
    assert web.parse_query("  \n", "fb") == "fb"


def test_derive_query_falls_back_to_the_request(monkeypatch):
    async def blank(engine, prompt, system=None):
        return ""

    monkeypatch.setattr(web, "call_llm", blank)
    query = asyncio.run(web.derive_query(ENGINE, "how much do cats sleep", "English"))
    assert query == "how much do cats sleep"


def test_brief_prompt_separates_read_pages_from_snippets():
    prompt = web.build_brief_prompt(_research(), "write about cat sleep")
    assert '<source n="1" status="read" url="https://site1.example/page">' in prompt
    assert "Full text one." in prompt
    # A failed read and an unfetched result both go as snippets
    assert '<source n="2" status="snippet"' in prompt
    assert "snippet 2" in prompt
    assert '<source n="3" status="snippet"' in prompt
    assert "## Proposed outline" in prompt
    assert "searched 2026-11-11" in prompt


def test_brief_prompt_for_a_refactor_mentions_the_original():
    prompt = web.build_brief_prompt(_research(), "update it", original_title="Old post")
    assert '"Old post"' in prompt
    assert "structure of the existing post" in prompt


def test_unknown_citations():
    brief = "- Fact [1]\n- Other [2, 7]\n- Bad [9]\n- Year 2026"
    assert web.unknown_citations(brief, _research()) == [7, 9]


def test_writer_context_lists_sources_with_status():
    context = web.writer_context("## Facts\n- Fact [1]", _research())
    assert context.startswith("## Facts")
    assert "[1] Result 1 — https://site1.example/page (read)" in context
    assert "[2] Result 2 — https://site2.example/page (snippet only (read failed: HTTP 403))" in context
    assert "[3] Result 3 — https://site3.example/page (snippet only (not fetched))" in context


def test_save_log(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "RESEARCH_DIR", tmp_path / "research")
    path = web.save_log(_research(), "## Facts\n- x [1]", "write it", tmp_path / "my-post.md")
    assert path == tmp_path / "research" / "my-post.md"
    text = path.read_text()
    assert "- Query: cat sleep" in text
    assert "- Pages read: 1 of 3 results" in text
    assert "https://site1.example/page (read)" in text


def test_build_message_adds_the_brief_only_when_given():
    plain = drafting.build_message([], "write it")
    assert "<research>" not in plain
    message = drafting.build_message([], "write it", research="## Facts\n- x [1]")
    assert "<research>\n## Facts\n- x [1]\n</research>" in message
    assert "names that source in the sentence" in message
    assert "at most once" in message
    assert "Take facts only from it" in message
    assert message.endswith("write it")
    assert "refactor rules below come first" not in message


def test_refactor_with_research_keeps_facts_the_sources_do_not_mention(tmp_path):
    original = Post(tmp_path / "old.md", {"title": "Old"}, "Cats get HCM.", False)
    message = drafting.build_message([], "update it", original, research="## Facts\n- x [1]")
    assert "Take facts only from it" not in message
    assert "is not a reason to cut it" in message
    assert "refactor rules below come first" in message


# --- follow-up search -------------------------------------------------------

BRAVE = re.compile(r"https://api\.search\.brave\.com/.*")
FIRST_BRIEF = "## Facts\n- Iraurita may be a brand, per a Reddit comment [1]"
FINAL_BRIEF = "## Facts\n- Iraurita is an iridium-coated tip [4]\n## Unresolved\n- none"


def _brave_by_query(answers):
    """Brave callback answering each query from `answers` (an int = that many results)."""
    def callback(request):
        answer = answers[request.url.params["q"]]
        if isinstance(answer, int):
            return httpx.Response(answer)
        return httpx.Response(200, json={"web": {"results": [
            {"title": f"Result {u}", "url": u, "description": f"About {u}"} for u in answer
        ]}})
    return callback


def _followup_llm(decision, seen):
    """Fake LLM: the follow-up decision answers `decision`, briefs come in order."""
    async def fake(engine, prompt, system=None):
        if system == prompts.text("research.followup_system"):
            seen.setdefault("decisions", []).append(prompt)
            return decision
        seen.setdefault("briefs", []).append(prompt)
        return FINAL_BRIEF
    return fake


def _first_round(httpx_mock, answers):
    httpx_mock.add_callback(_brave_by_query(answers), url=BRAVE, is_reusable=True)
    httpx_mock.add_response(url=re.compile(r"https://site\d\.example/.*"), html=PAGE, is_reusable=True)
    return asyncio.run(web.gather("iraurita pen nib", "key", "English"))


def _brave_requests(httpx_mock):
    return [r for r in httpx_mock.get_requests() if r.url.host == "api.search.brave.com"]


def _brave_queries(httpx_mock):
    return [r.url.params["q"] for r in _brave_requests(httpx_mock)]


def _brave_locales(httpx_mock):
    return [(r.url.params["country"], r.url.params["search_lang"]) for r in _brave_requests(httpx_mock)]


FIRST_URLS = ["https://site1.example/a", "https://site2.example/b", "https://site3.example/c"]


def test_follow_up_not_needed_keeps_the_first_round(httpx_mock, monkeypatch):
    found = _first_round(httpx_mock, {"iraurita pen nib": FIRST_URLS})
    seen = {}
    monkeypatch.setattr(web, "call_llm", _followup_llm("NONE", seen))

    research, brief = asyncio.run(web.follow_up(found, FIRST_BRIEF, "write about Iraurita", ENGINE, "key"))

    assert brief == FIRST_BRIEF
    assert "briefs" not in seen  # no rewrite
    assert _brave_queries(httpx_mock) == ["iraurita pen nib"]  # no extra search
    assert [r.url for r in research.results] == FIRST_URLS
    assert research.followup.query == ""
    assert research.followup.outcome.startswith("not needed")
    # The decision sees the request, the sources and the brief
    assert "write about Iraurita" in seen["decisions"][0]
    assert '<source n="1" status="read" url="https://site1.example/a">' in seen["decisions"][0]
    assert FIRST_BRIEF in seen["decisions"][0]


def test_inconclusive_first_round_runs_exactly_one_more_search(httpx_mock, monkeypatch, tmp_path):
    followup_urls = ["https://site2.example/b/", "https://site4.example/d", "https://site5.example/e"]
    found = _first_round(httpx_mock, {
        "iraurita pen nib": FIRST_URLS, "Iraurita iridium nib listing": followup_urls,
    })
    seen = {}
    decision = ("Uncertainty: the sources do not establish what Iraurita means.\n"
                "Query: Iraurita iridium nib listing")
    monkeypatch.setattr(web, "call_llm", _followup_llm(decision, seen))

    research, brief = asyncio.run(web.follow_up(found, FIRST_BRIEF, "write about Iraurita", ENGINE, "key"))

    assert _brave_queries(httpx_mock) == ["iraurita pen nib", "Iraurita iridium nib listing"]
    # A two-line answer (no Language:) searches where the first search did
    assert _brave_locales(httpx_mock) == [("US", "en"), ("US", "en")]
    assert len(seen["decisions"]) == 1 and len(seen["briefs"]) == 1
    assert brief == FINAL_BRIEF
    # site2 came back with a trailing slash: same page, kept once; new ones numbered after
    assert [(r.rank, r.url) for r in research.results] == [
        (1, FIRST_URLS[0]), (2, FIRST_URLS[1]), (3, FIRST_URLS[2]),
        (4, "https://site4.example/d"), (5, "https://site5.example/e"),
    ]
    assert research.status(research.results[3]) == "read"
    assert [r.rank for r in research.first_round] == [1, 2, 3]
    assert web.unknown_citations(brief, research) == []
    assert found.followup is None and len(found.results) == 3  # the first round is untouched

    prompt = seen["briefs"][0]
    assert 'top 3 web search results for "iraurita pen nib"' in prompt
    assert '<source n="5" status="read" url="https://site5.example/e">' in prompt
    assert 'Sources 4 to 5 come from a follow-up search for "Iraurita iridium nib listing"' in prompt
    assert "what Iraurita means" in prompt
    assert "what remains unresolved" in prompt

    sources = web.sources_markdown(research)
    assert "[4] Result https://site4.example/d — https://site4.example/d (read)" in sources

    monkeypatch.setattr(web, "RESEARCH_DIR", tmp_path / "research")
    log = web.save_log(research, brief, "write about Iraurita", tmp_path / "iraurita.md").read_text()
    assert "- Query: iraurita pen nib" in log
    assert "- Follow-up query: Iraurita iridium nib listing" in log
    assert "- Region: US, language en\n" in log
    assert "- Follow-up region: US, language en\n" in log
    assert "- Follow-up search: added 2 sources [4]–[5], 2 read" in log


def test_follow_up_search_failure_keeps_the_first_round_brief(httpx_mock, monkeypatch, tmp_path):
    found = _first_round(httpx_mock, {"iraurita pen nib": FIRST_URLS, "Iraurita 铱金": 500})
    seen = {}
    monkeypatch.setattr(web, "call_llm", _followup_llm(
        "Uncertainty: the meaning of the term.\nQuery: Iraurita 铱金", seen))

    research, brief = asyncio.run(web.follow_up(found, FIRST_BRIEF, "write about Iraurita", ENGINE, "key"))

    assert _brave_queries(httpx_mock) == ["iraurita pen nib", "Iraurita 铱金"]
    assert brief == FIRST_BRIEF
    assert "briefs" not in seen
    assert [r.url for r in research.results] == FIRST_URLS
    assert research.followup.query == "Iraurita 铱金"
    assert research.followup.outcome == "failed: Brave Search failed: HTTP 500; kept the first-round brief"

    monkeypatch.setattr(web, "RESEARCH_DIR", tmp_path / "research")
    log = web.save_log(research, brief, "r", tmp_path / "iraurita.md").read_text()
    assert "- Follow-up query: Iraurita 铱金" in log
    assert "- Follow-up search: failed: Brave Search failed: HTTP 500" in log


def test_follow_up_language_sets_its_own_search_locale(httpx_mock, monkeypatch, tmp_path):
    found = _first_round(httpx_mock, {
        "iraurita pen nib": FIRST_URLS, "铱金 笔尖 Iraurita": ["https://site4.example/d"],
    })
    seen = {}
    monkeypatch.setattr(web, "call_llm", _followup_llm(
        "Uncertainty: the original term.\nQuery: 铱金 笔尖 Iraurita\nLanguage: zh-Hans", seen))

    research, brief = asyncio.run(web.follow_up(found, FIRST_BRIEF, "write about Iraurita", ENGINE, "key"))

    assert _brave_locales(httpx_mock) == [("US", "en"), ("CN", "zh-hans")]
    assert (research.followup.country, research.followup.search_lang) == ("CN", "zh-hans")
    # The brief and the post keep the first search's language and locale
    assert (research.language, research.country, research.search_lang) == ("English", "US", "en")
    assert "Write a research brief in English" in seen["briefs"][0]
    assert brief == FINAL_BRIEF

    monkeypatch.setattr(web, "RESEARCH_DIR", tmp_path / "research")
    log = web.save_log(research, brief, "r", tmp_path / "iraurita.md").read_text()
    assert "- Region: US, language en\n" in log
    assert "- Follow-up region: CN, language zh-hans (asked for zh-Hans)\n" in log


@pytest.mark.parametrize("language", ["Klingon!!", "tlh", "zz-ZZ"])
def test_bad_follow_up_language_uses_the_first_locale(httpx_mock, monkeypatch, tmp_path, language):
    found = _first_round(httpx_mock, {
        "iraurita pen nib": FIRST_URLS, "iraurita iridium": ["https://site4.example/d"],
    })
    seen = {}
    monkeypatch.setattr(web, "call_llm", _followup_llm(
        f"Uncertainty: x\nQuery: iraurita iridium\nLanguage: {language}", seen))

    research, brief = asyncio.run(web.follow_up(found, FIRST_BRIEF, "r", ENGINE, "key"))

    assert _brave_locales(httpx_mock) == [("US", "en"), ("US", "en")]
    assert (research.language, research.country, research.search_lang) == ("English", "US", "en")
    assert "Write a research brief in English" in seen["briefs"][0]
    assert brief == FINAL_BRIEF
    monkeypatch.setattr(web, "RESEARCH_DIR", tmp_path / "research")
    log = web.save_log(research, brief, "r", tmp_path / "x.md").read_text()
    assert (f'- Follow-up region: US, language en (asked for "{language}": not supported, '
            "used the first search's)") in log


@pytest.mark.parametrize("code, locale", [
    ("zh", ("CN", "zh-hans")), ("zh-Hant", ("TW", "zh-hant")), ("zh_TW", ("TW", "zh-hant")),
    ("zh-HK", ("HK", "zh-hant")), ("ja", ("JP", "jp")), ("pt-PT", ("PT", "pt-pt")),
    ("pt", ("BR", "pt-br")), ("en-GB", ("GB", "en-gb")), ("de-AT", ("AT", "de")),
    ("de-XX", ("DE", "de")),  # a region Brave lacks: the language's own
    ("ca", ("ALL", "ca")),  # no country for the language: worldwide
    ("tlh", None), ("Chinese", None), ("", None), ("zh-", None),
])
def test_locale_for_code(code, locale):
    assert web.locale_for_code(code) == locale


def test_follow_up_skips_a_repeat_of_the_first_query(httpx_mock, monkeypatch):
    found = _first_round(httpx_mock, {"iraurita pen nib": FIRST_URLS})
    monkeypatch.setattr(web, "call_llm", _followup_llm("Uncertainty: x\nQuery: Iraurita pen-nib", {}))

    research, brief = asyncio.run(web.follow_up(found, FIRST_BRIEF, "r", ENGINE, "key"))

    assert _brave_queries(httpx_mock) == ["iraurita pen nib"]
    assert brief == FIRST_BRIEF
    assert research.followup.outcome == "skipped: same as the first query"


def test_failed_rewrite_drops_the_follow_up_sources(httpx_mock, monkeypatch):
    found = _first_round(httpx_mock, {
        "iraurita pen nib": FIRST_URLS, "iraurita iridium": ["https://site4.example/d"],
    })

    async def fake(engine, prompt, system=None):
        if system == prompts.text("research.followup_system"):
            return "Query: iraurita iridium"
        raise httpx.ConnectError("down")

    monkeypatch.setattr(web, "call_llm", fake)
    research, brief = asyncio.run(web.follow_up(found, FIRST_BRIEF, "r", ENGINE, "key"))
    assert brief == FIRST_BRIEF
    assert [r.url for r in research.results] == FIRST_URLS
    assert research.followup.first_rank == 0
    assert "brief rewrite failed" in research.followup.outcome


def test_parse_followup_needs_a_query_line():
    assert web.parse_followup("NONE") == ("", "", "")
    assert web.parse_followup("## Facts\n- Cats sleep [1]") == ("", "", "")
    assert web.parse_followup("Query: NONE\nLanguage: zh") == ("", "", "")
    assert web.parse_followup('**Uncertainty:** what it means\n**Query:** "iraurita iridium"') == (
        "iraurita iridium", "what it means", "",
    )
    assert web.parse_followup("Uncertainty: x\nQuery: 铱金 笔尖\nLanguage: `zh-Hans`") == (
        "铱金 笔尖", "x", "zh-Hans",
    )


def test_follow_up_note_reaches_an_override_without_the_placeholder():
    # A saved brief override with its own headings and no {followup}
    prompts.activate({"research.brief": "Request: {request}\n{sources}\n# Key points\n# Open issues"})
    found = _research()
    found.results.append(web.SearchResult(4, "New", "https://site4.example/page", "snippet 4"))
    found.followup = web.FollowUp(query="cat sleep winter", reason="Winter is unclear.", first_rank=4)
    prompt = web.build_brief_prompt(found, "write it")
    assert prompt.startswith("Request: write it")
    assert '<source n="4" status="snippet" url="https://site4.example/page">' in prompt
    assert prompt.endswith("what remains unresolved, and do not present a guess, a hypothesis "
                           "or a single commenter's opinion as the answer.")
    assert prompt.count("Sources 4 to 4") == 1


# --- screens ----------------------------------------------------------------


def _posts(tmp_path):
    out = []
    for i in range(7):
        path = tmp_path / f"p{i}.md"
        path.write_text("body")
        out.append(Post(path, {"title": f"P{i}"}, "body", False,
                        date=datetime.now() - timedelta(days=i + 1), mtime=path.stat().st_mtime))
    return out


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


def test_prompt_screen_research_is_off_without_a_key():
    screen = {}

    async def keys(app, pilot):
        screen["s"] = app.screen
        assert app.screen.query_one("#draft-research", Checkbox).disabled
        await pilot.press(*"hello", "ctrl+s")

    def factory():
        return ed.DraftPromptScreen([], None, None, authors=["Ana"], profiles={"Ana": "Warm."})

    assert _run(factory, keys) == "hello"
    assert screen["s"].research_query is None


def test_prompt_screen_returns_the_search_query():
    screen = {}

    async def keys(app, pilot):
        screen["s"] = app.screen
        assert app.screen.query_one("#draft-query", Input).disabled
        await pilot.press(*"hello")
        app.screen.query_one("#draft-research", Checkbox).value = True
        await pilot.pause()
        query = app.screen.query_one("#draft-query", Input)
        assert not query.disabled
        query.value = "cat sleep"
        await pilot.press("ctrl+s")

    def factory():
        return ed.DraftPromptScreen([], None, None, authors=["Ana"], profiles={"Ana": "Warm."},
                                    search_key="key")

    assert _run(factory, keys) == "hello"
    assert screen["s"].research_query == "cat sleep"


def test_research_screen_derives_the_query_and_writes_the_brief(monkeypatch):
    seen = {}

    async def fake_llm(engine, prompt, system=None):
        if system == prompts.text("research.brief_system"):
            seen["brief_prompt"] = prompt
            return "## Facts\n- Cats sleep a lot [1]"
        return "cat sleep hours"

    async def fake_gather(query, api_key, language):
        seen.update(query=query, key=api_key, language=language)
        return _research()

    monkeypatch.setattr(web, "call_llm", fake_llm)
    monkeypatch.setattr(web, "gather", fake_gather)

    async def keys(app, pilot):
        await pilot.pause(0.3)

    result = _run(lambda: ed.ResearchScreen("write about cat sleep", "", ENGINE, "key", "English"), keys)
    found, brief = result
    assert brief == "## Facts\n- Cats sleep a lot [1]"
    assert found.pages_read == 1
    assert seen["query"] == "cat sleep hours"
    assert seen["key"] == "key"
    assert "write about cat sleep" in seen["brief_prompt"]


def test_research_screen_reports_failure(monkeypatch):
    async def failing(query, api_key, language):
        raise web.ResearchError("Brave Search failed: HTTP 429")

    monkeypatch.setattr(web, "gather", failing)

    async def keys(app, pilot):
        await pilot.pause(0.3)

    assert _run(lambda: ed.ResearchScreen("r", "q", ENGINE, "key", "English"), keys) == (
        None, "Brave Search failed: HTTP 429",
    )


def test_brief_review_returns_the_edited_brief():
    async def keys(app, pilot):
        area = app.screen.query_one("#brief-text", TextArea)
        assert "[green]" not in area.text
        area.text = "## Facts\n- edited [1]"
        await pilot.press("ctrl+s")

    assert _run(lambda: ed.BriefReviewScreen(_research(), "## Facts\n- x [1]"), keys) == (
        "## Facts\n- edited [1]"
    )


def test_brief_review_warns_about_unknown_citations():
    async def keys(app, pilot):
        assert "[9]" in str(app.screen.query_one("#brief-error").render())
        await pilot.press("escape")

    assert _run(lambda: ed.BriefReviewScreen(_research(), "- x [9]"), keys) is None


def test_brief_review_after_failure_offers_writing_without_research():
    async def keys(app, pilot):
        assert not app.screen.query("#brief-text")
        await pilot.press("ctrl+s")  # nothing to approve
        assert isinstance(app.screen, ed.BriefReviewScreen)
        await pilot.click("#btn-brief-skip")

    assert _run(lambda: ed.BriefReviewScreen(None, "HTTP 429"), keys) == ed.WITHOUT_RESEARCH


def test_wait_screen_sends_the_brief_and_keeps_the_research(tmp_path, monkeypatch):
    seen = {}

    async def fake_ask(engine, message, system=None):
        seen.setdefault("message", message)
        return "Cat Title\n\nCat body."

    monkeypatch.setattr(drafting, "call_llm", fake_ask)
    monkeypatch.setattr(web, "RESEARCH_DIR", tmp_path / "research")
    posts_dir = tmp_path / "posts"
    posts_dir.mkdir()

    async def keys(app, pilot):
        await pilot.pause(0.3)

    path = _run(lambda: ed.DraftWaitScreen(
        "write cats", _posts(posts_dir), posts_dir, ENGINE,
        research=_research(), brief="## Facts\n- Cats sleep [1]",
    ), keys)
    assert path.name == "cat-title.md"
    assert "<research>\n## Facts\n- Cats sleep [1]\n\n## Sources" in seen["message"]
    assert "https://site1.example/page (read)" in seen["message"]
    log = tmp_path / "research" / "cat-title.md"
    assert "write cats" in log.read_text()
    assert frontmatter.load(str(path)).content == "Cat body."


def _h_flow(tmp_path, monkeypatch, review):
    from unittest.mock import MagicMock

    from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
    from hugin.tui.review import HuginScreen

    seen = {}

    async def fake_writer(engine, message, system=None):
        seen["message"] = message
        return "From Research\n\nText."

    async def fake_gather(query, api_key, language):
        seen["query"] = query
        return _research()

    async def fake_llm(engine, prompt, system=None):
        return "## Facts\n- Cats sleep [1]"

    monkeypatch.setattr(drafting, "call_llm", fake_writer)
    monkeypatch.setattr(web, "call_llm", fake_llm)
    monkeypatch.setattr(web, "gather", fake_gather)
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
            await pilot.press("h")
            await pilot.pause()
            await pilot.press(*"cats")
            assert app.screen.query_one("#draft-review", Checkbox).disabled
            app.screen.query_one("#draft-research", Checkbox).value = True
            app.screen.query_one("#draft-query", Input).value = "cat sleep"
            await pilot.pause()
            app.screen.query_one("#draft-review", Checkbox).value = review
            await pilot.press("ctrl+s")
            await pilot.pause(0.5)
            if review:
                assert isinstance(app.screen, ed.BriefReviewScreen)
                assert app.screen.query_one("#btn-brief-send", Button)
                await pilot.press("ctrl+s")
                await pilot.pause(0.5)
            assert not isinstance(app.screen, ed.BriefReviewScreen)
            assert (posts_dir / "from-research.md").exists()

    asyncio.run(go())
    assert seen["query"] == "cat sleep"
    assert "<research>\n## Facts\n- Cats sleep [1]" in seen["message"]
    assert (tmp_path / "research" / "from-research.md").exists()


def test_h_key_with_research_writes_without_asking(tmp_path, monkeypatch):
    _h_flow(tmp_path, monkeypatch, review=False)


def test_h_key_with_research_can_show_the_brief_first(tmp_path, monkeypatch):
    _h_flow(tmp_path, monkeypatch, review=True)


# --- refactor check ---------------------------------------------------------

ORIGINAL_BODY = (
    "Intro with [old breeds](/5-old-breeds/) and [treats](https://amzn.to/abc \"treats\").\n\n"
    "![a cat](/img/cat.avif)\n\n"
    "See the [club](https://club.example/stats) and [kids](/cats-for-kids/).\n\n"
    + "word " * 100
)


def test_refactor_losses_lists_what_went_missing():
    new = "Intro with [old breeds](/5-old-breeds/).\n\n![a cat](/img/cat.avif)\n\n" + "word " * 50
    # Links to own posts and affiliate links are left out on purpose
    assert drafting.refactor_losses(ORIGINAL_BODY, new) == [
        ("external link", "https://club.example/stats  “club”"),
        ("length", "56 words; the original has 114"),
    ]
    assert drafting.refactor_losses(ORIGINAL_BODY, ORIGINAL_BODY) == []


def _refactor_wait(tmp_path, keys_fn):
    posts_dir = tmp_path / "posts"
    posts_dir.mkdir()
    posts = _posts(posts_dir)
    original = Post(posts_dir / "old.md", {"title": "Old", "image": "/img/cover.avif",
                                           "categories": ["Breeds"]},
                    ORIGINAL_BODY, False)
    return posts_dir, _run(lambda: ed.DraftWaitScreen(
        "update it", posts, posts_dir, ENGINE, original=original,
    ), keys_fn)


def _short_answer(monkeypatch):
    async def fake_ask(engine, message, system=None):
        return "New Title\n\nShort text without links."

    monkeypatch.setattr(drafting, "call_llm", fake_ask)


def test_refactor_with_losses_asks_before_saving(tmp_path, monkeypatch):
    _short_answer(monkeypatch)

    async def keys(app, pilot):
        await pilot.pause(0.3)
        assert isinstance(app.screen, ed.DraftCheckScreen)
        shown = str(app.screen.query_one("#check-list Static").render())
        assert "club.example" in shown and "/img/cat.avif" in shown
        assert "/cats-for-kids/" not in shown and "amzn.to/abc" not in shown
        await pilot.press("ctrl+s")

    posts_dir, path = _refactor_wait(tmp_path, keys)
    assert path.name == "new-title.md"
    meta = frontmatter.load(str(path)).metadata
    assert meta["image"] == "/img/cover.avif"
    assert "thumbnail" not in meta


def test_refactor_with_losses_can_be_discarded(tmp_path, monkeypatch):
    _short_answer(monkeypatch)

    async def keys(app, pilot):
        await pilot.pause(0.3)
        assert isinstance(app.screen, ed.DraftCheckScreen)
        await pilot.press("escape")

    posts_dir, path = _refactor_wait(tmp_path, keys)
    assert path is None
    assert not (posts_dir / "new-title.md").exists()


def test_unlink_internal_keeps_images_and_source_links():
    assert drafting.unlink_internal(ORIGINAL_BODY).startswith(
        "Intro with old breeds and treats.\n\n![a cat](/img/cat.avif)\n\n"
        "See the [club](https://club.example/stats) and kids."
    )
    assert drafting.recreatable_links(ORIGINAL_BODY) == (2, 1)


def test_refactor_writer_never_sees_own_or_affiliate_links(tmp_path):
    original = Post(tmp_path / "old.md", {"title": "Old"}, ORIGINAL_BODY, False)
    message = drafting.build_message([], "update it", original)
    assert "/5-old-breeds/" not in message and "amzn.to" not in message
    assert "](https://club.example/stats)" in message and "](/img/cat.avif)" in message
    assert "do not add any" in message


def test_refactor_draft_drops_links_the_writer_kept(tmp_path, monkeypatch):
    async def fake_ask(engine, message, system=None):
        return ("New Title\n\n" + ORIGINAL_BODY.replace("Intro", "Intro [new](/invented/)"))

    monkeypatch.setattr(drafting, "call_llm", fake_ask)

    async def keys(app, pilot):
        await pilot.pause(0.3)

    posts_dir, path = _refactor_wait(tmp_path, keys)
    body = frontmatter.load(str(path)).content
    assert "](/" not in body.replace("](/img/cat.avif)", "")
    assert "amzn.to" not in body
    assert "[club](https://club.example/stats)" in body


# --- search cache -----------------------------------------------------------


def _search_once():
    async def go():
        async with httpx.AsyncClient() as client:
            return await web.brave_search(client, "cat sleep", "key", "US", "en")

    return asyncio.run(go())


def test_a_repeated_search_comes_from_the_cache(httpx_mock):
    httpx_mock.add_response(url=re.compile(r"https://api\.search\.brave\.com/.*"), json=_brave(2))
    first = _search_once()
    # pytest-httpx refuses a second request: the cache must answer it
    assert _search_once() == first
    assert len(httpx_mock.get_requests()) == 1
    assert web.cached_search("cat sleep", "US", "en") is not None
    assert web.cached_search("cat sleep", "BR", "pt-br") is None  # other parameters


def test_the_cache_expires_after_a_day(httpx_mock, monkeypatch):
    httpx_mock.add_response(url=re.compile(r"https://api\.search\.brave\.com/.*"), json=_brave(1),
                            is_reusable=True)
    _search_once()
    now = web.time.time()
    monkeypatch.setattr(web.time, "time", lambda: now + web.SEARCH_CACHE_SECONDS + 1)
    assert web.cached_search("cat sleep", "US", "en") is None
    _search_once()
    assert len(httpx_mock.get_requests()) == 2


def test_failures_are_not_cached(httpx_mock):
    httpx_mock.add_response(url=re.compile(r"https://api\.search\.brave\.com/.*"), status_code=429)
    with pytest.raises(httpx.HTTPStatusError):
        _search_once()
    assert web.cached_search("cat sleep", "US", "en") is None


def test_a_corrupt_cache_entry_is_ignored(httpx_mock):
    httpx_mock.add_response(url=re.compile(r"https://api\.search\.brave\.com/.*"), json=_brave(1),
                            is_reusable=True)
    _search_once()
    for entry in web.SEARCH_CACHE_DIR.iterdir():
        entry.write_text("{not json")
    assert web.cached_search("cat sleep", "US", "en") is None
    assert [r.rank for r in _search_once()] == [1]
