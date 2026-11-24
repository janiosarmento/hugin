"""Web research for drafts (`h`, `R`): search, read a few pages, brief the writer.

The flow mirrors what research-first writing tools document: look at what
ranks for the topic, read some of it, and turn that into a brief (search
intent, recurring topics, open questions, gaps, sourced facts, outline) that
the author reviews before the writer sees it.

Search results come from the Brave Search API; pages are fetched and reduced
to text here. Every source keeps its rank, URL and read status, so the brief
never presents a page that could not be read as verified.

When the first brief leaves a material question open (what a term means, two
competing explanations), one follow-up search runs and the brief is written
again from both rounds; see follow_up().
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx

from hugin import prompts
from hugin.drafting import LLM_ERRORS
from hugin.engines import CONFIG_DIR, _get_api_key
from hugin.fsutil import atomic_write_text
from hugin.log import log_exception
from hugin.llm import call_llm

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
# Vault path of the Brave Search key (secrets-resolver), like `<engine>.api_key`
SEARCH_SECRET = "brave.search"

# Working limits for a first version; tune after trying real posts.
N_RESULTS = 5
N_PAGES = 3
PAGE_CHARS = 8000  # text kept per page read
MIN_PAGE_CHARS = 300  # less than this is not a readable article (JS shell, paywall)
MAX_PAGE_BYTES = 3_000_000
SNIPPET_CHARS = 400
SEARCH_TIMEOUT = 15
PAGE_TIMEOUT = 15
MAX_QUERY_CHARS = 120

RESEARCH_DIR = CONFIG_DIR / "research"

# Brave answers kept on disk, so repeated searches (tests, retries, the same
# post refactored twice) do not spend the monthly quota
SEARCH_CACHE_DIR = CONFIG_DIR / "cache" / "brave"
SEARCH_CACHE_SECONDS = 24 * 60 * 60

# Without Accept, some sites (Wikipedia among them) answer 403
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Hugin/1.0)",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
}

# detect_language() name -> (Brave country, Brave search_lang)
LOCALES = {
    "Portuguese": ("BR", "pt-br"),
    "English": ("US", "en"),
    "Spanish": ("ES", "es"),
    "French": ("FR", "fr"),
}

# Brave Search's accepted `search_lang` and `country` values (API reference)
BRAVE_LANGS = frozenset(
    "ar eu bn bg ca zh-hans zh-hant hr cs da nl en en-gb et fi fr gl de gu he hi "
    "hu is it jp kn ko lv lt ms ml mr nb pl pt-br pt-pt pa ro ru sr sk sl es sv "
    "ta te th tr uk vi".split()
)
BRAVE_COUNTRIES = frozenset(
    "AR AU AT BE BR CA CL DK FI FR DE HK IN ID IT JP KR MY MX NL NZ NO CN PL PT "
    "PH RU SA ZA ES SE CH TW TR GB US".split()
)
ALL_COUNTRIES = "ALL"  # Brave's worldwide region
# The region a follow-up in this search_lang uses when its code names none;
# a language without one here searches worldwide
LANG_COUNTRY = {
    "en": "US", "en-gb": "GB", "pt-br": "BR", "pt-pt": "PT", "es": "ES", "fr": "FR",
    "de": "DE", "it": "IT", "nl": "NL", "zh-hans": "CN", "zh-hant": "TW", "jp": "JP",
    "ko": "KR", "ru": "RU", "pl": "PL", "sv": "SE", "da": "DK", "fi": "FI", "nb": "NO",
    "tr": "TR", "ar": "SA", "ms": "MY", "hi": "IN",
}
# Language codes as a model writes them (BCP 47) -> Brave's spelling
_LANG_ALIASES = {"ja": "jp", "no": "nb", "iw": "he"}
_LANG_CODE = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]{2,8}){0,3}")

# What a failed search or page fetch can raise
FETCH_ERRORS = (httpx.HTTPError, ValueError, KeyError, TypeError)


class ResearchError(Exception):
    """Raised when the research cannot run at all; message is user-facing."""


@dataclass
class SearchResult:
    rank: int
    title: str
    url: str
    snippet: str


@dataclass
class PageRead:
    """Outcome of reading one result: text when read, an error otherwise."""

    url: str
    text: str = ""
    error: str | None = None
    skipped: bool = False  # readable, but over the N_PAGES limit

    @property
    def ok(self) -> bool:
        return self.error is None and not self.skipped and bool(self.text)


@dataclass
class FollowUp:
    """The one extra search after the first brief, and what came of it."""

    query: str = ""
    reason: str = ""  # the uncertainty the search is meant to settle
    language: str = ""  # the code the decision asked for; "" = none
    # Where the search ran ("" = it did not run); the first search's locale
    # unless `language` resolved to a supported one
    country: str = ""
    search_lang: str = ""
    outcome: str = ""  # for the review screen and the research log
    first_rank: int = 0  # rank of the first source it added; 0 = none kept


@dataclass
class Research:
    query: str
    language: str
    country: str
    search_lang: str
    fetched_at: datetime
    results: list[SearchResult] = field(default_factory=list)
    pages: dict[str, PageRead] = field(default_factory=dict)
    followup: FollowUp | None = None

    def status(self, result: SearchResult) -> str:
        """'read', or why only the search snippet is available."""
        page = self.pages.get(result.url)
        if page is None:
            return "snippet only (not fetched)"
        if page.ok:
            return "read"
        if page.skipped:
            return "snippet only (over the page limit)"
        return f"snippet only (read failed: {page.error})"

    @property
    def pages_read(self) -> int:
        return sum(1 for p in self.pages.values() if p.ok)

    @property
    def first_round(self) -> list[SearchResult]:
        """Results of the first search (all of them without a kept follow-up)."""
        if self.followup and self.followup.first_rank:
            return [r for r in self.results if r.rank < self.followup.first_rank]
        return self.results


def search_api_key() -> str | None:
    """The Brave Search key from the vault, or None when it is not set."""
    return _get_api_key("brave", SEARCH_SECRET)


def locale_for(language: str) -> tuple[str, str]:
    return LOCALES.get(language, LOCALES["English"])


def locale_for_code(code: str) -> tuple[str, str] | None:
    """(country, search_lang) for a language code like zh, zh-Hant, ja or pt-PT.

    None when the code is malformed or Brave has no such language. The region
    comes from the code when Brave supports it, else from the language itself
    (LANG_COUNTRY), else worldwide; never from another search.
    """
    norm = (code or "").strip().lower().replace("_", "-")
    if not _LANG_CODE.fullmatch(norm):
        return None
    lang, *subtags = norm.split("-")
    lang = _LANG_ALIASES.get(lang, lang)
    script = next((t for t in subtags if len(t) == 4), "")
    region = next((t.upper() for t in subtags if len(t) == 2), "")
    if lang == "zh":
        search_lang = "zh-hant" if script == "hant" or region in ("TW", "HK", "MO") else "zh-hans"
    elif lang == "pt":
        search_lang = "pt-pt" if region == "PT" else "pt-br"
    elif lang == "en":
        search_lang = "en-gb" if region == "GB" else "en"
    else:
        search_lang = lang
    if search_lang not in BRAVE_LANGS:
        return None
    country = region if region in BRAVE_COUNTRIES else LANG_COUNTRY.get(search_lang, ALL_COUNTRIES)
    return country, search_lang


# --- search -----------------------------------------------------------------


def _clean(text: str) -> str:
    """Brave wraps matches in <strong>; drop tags and entities' markup."""
    from html import unescape

    return " ".join(unescape(re.sub(r"<[^>]+>", "", text or "")).split())


# The blog's source domains (project [research]); set when its settings load
_trusted: list[str] = []
_excluded: list[str] = []


def activate_sources(trusted: list[str], excluded: list[str]) -> None:
    """Use these source domains from now on (see ResearchSettings)."""
    _trusted[:] = [_domain(d) for d in trusted if _domain(d)]
    _excluded[:] = [_domain(d) for d in excluded if _domain(d)]


def _domain(text: str) -> str:
    text = text.strip().lower()
    text = text.split("://", 1)[-1].split("/", 1)[0]
    return text.removeprefix("www.")


def _on(url: str, domains: list[str]) -> bool:
    host = _domain(url)
    return any(host == d or host.endswith("." + d) for d in domains)


def is_trusted(url: str) -> bool:
    return _on(url, _trusted)


def parse_brave(data: dict, count: int = N_RESULTS) -> list[SearchResult]:
    """Results in Brave's order, trusted domains first, excluded ones left out."""
    results = []
    items = [
        item for item in (data.get("web") or {}).get("results") or []
        if not _on(str(item.get("url") or ""), _excluded)
    ]
    items.sort(key=lambda item: not is_trusted(str(item.get("url") or "")))  # stable
    for item in items:
        url = item.get("url")
        if not url or not url.startswith(("http://", "https://")):
            continue
        results.append(SearchResult(
            rank=len(results) + 1,
            title=_clean(item.get("title", "")) or url,
            url=url,
            snippet=_clean(item.get("description", ""))[:SNIPPET_CHARS],
        ))
        if len(results) == count:
            break
    return results


def _cache_path(params: dict) -> Path:
    key = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return SEARCH_CACHE_DIR / f"{hashlib.sha256(key.encode()).hexdigest()[:32]}.json"


def _search_params(query: str, country: str, search_lang: str, count: int) -> dict:
    return {"q": query, "count": count, "country": country, "search_lang": search_lang}


def cached_search(query: str, country: str, search_lang: str, count: int = N_RESULTS) -> dict | None:
    """The Brave answer saved for these parameters, when under SEARCH_CACHE_SECONDS old."""
    path = _cache_path(_search_params(query, country, search_lang, count))
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(entry["saved_at"]) < SEARCH_CACHE_SECONDS:
            return entry["data"]
    except (OSError, ValueError, KeyError, TypeError):
        pass  # missing, unreadable or corrupt: search again
    return None


def _save_search(params: dict, data: dict) -> None:
    try:
        SEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_text(_cache_path(params), json.dumps(
            {"saved_at": time.time(), "params": params, "data": data}, ensure_ascii=False,
        ))
    except OSError:
        log_exception("brave search cache")  # a cache that cannot be written is not an error


async def brave_search(
    client: httpx.AsyncClient, query: str, api_key: str,
    country: str, search_lang: str, count: int = N_RESULTS,
) -> list[SearchResult]:
    """Search results, from the 24-hour cache when the same search was made.

    With source domains set, more results are asked for, so that leaving
    out excluded ones and moving trusted ones up still fills `count`.
    """
    wanted = count
    count = min(20, count * 2) if (_trusted or _excluded) else count
    params = _search_params(query, country, search_lang, count)
    data = cached_search(query, country, search_lang, count)
    if data is None:
        response = await client.get(
            BRAVE_URL, params=params,
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            timeout=SEARCH_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        _save_search(params, data)
    return parse_brave(data, wanted)


# --- page reading -----------------------------------------------------------

_SKIP_TAGS = {"script", "style", "noscript", "nav", "header", "footer", "aside",
              "form", "svg", "iframe", "template", "button", "select"}
_BLOCK_TAGS = {"p", "div", "section", "article", "main", "li", "ul", "ol", "br",
               "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "blockquote",
               "pre", "figcaption", "dd", "dt"}
_VOID_TAGS = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "col", "area", "base"}


class _TextExtractor(HTMLParser):
    """Visible text of a page, with the <main>/<article> text kept apart."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._focus = 0
        self.all: list[str] = []
        self.focus: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _VOID_TAGS:
            if tag == "br":
                self._emit("\n")
            return
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in ("main", "article"):
            self._focus += 1
        if tag in _BLOCK_TAGS:
            self._emit("\n")
        if tag in ("h1", "h2", "h3", "h4") and not self._skip:
            self._emit("#" * int(tag[1]) + " ")

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS:
            return
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in ("main", "article"):
            self._focus = max(0, self._focus - 1)
        if tag in _BLOCK_TAGS:
            self._emit("\n")

    def handle_data(self, data):
        self._emit(data)

    def _emit(self, text: str) -> None:
        if self._skip:
            return
        self.all.append(text)
        if self._focus:
            self.focus.append(text)


def _tidy(parts: list[str]) -> str:
    lines = (" ".join(line.split()) for line in "".join(parts).splitlines())
    text = "\n".join(line for line in lines if line)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def html_to_text(html: str) -> str:
    """Readable text of an HTML page; the main/article part when it has enough."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except AssertionError:  # HTMLParser on badly broken markup
        pass
    focus = _tidy(parser.focus)
    return focus if len(focus) >= MIN_PAGE_CHARS else _tidy(parser.all)


async def fetch_page(client: httpx.AsyncClient, url: str) -> PageRead:
    try:
        response = await client.get(url, timeout=PAGE_TIMEOUT, follow_redirects=True,
                                    headers=_HEADERS)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        return PageRead(url, error=f"HTTP {e.response.status_code}")
    except httpx.TimeoutException:
        return PageRead(url, error="timeout")
    except httpx.HTTPError as e:
        return PageRead(url, error=type(e).__name__)
    kind = response.headers.get("content-type", "")
    if "html" not in kind and "text/plain" not in kind:
        return PageRead(url, error=f"not a web page ({kind.split(';')[0] or 'unknown type'})")
    if len(response.content) > MAX_PAGE_BYTES:
        return PageRead(url, error="page too large")
    text = html_to_text(response.text) if "html" in kind else response.text.strip()
    if len(text) < MIN_PAGE_CHARS:
        return PageRead(url, error="no readable text")
    return PageRead(url, text=text[:PAGE_CHARS])


async def _search(
    client: httpx.AsyncClient, query: str, api_key: str, country: str, search_lang: str,
) -> list[SearchResult]:
    try:
        return await brave_search(client, query, api_key, country, search_lang)
    except httpx.HTTPStatusError as e:
        raise ResearchError(f"Brave Search failed: HTTP {e.response.status_code}") from e
    except FETCH_ERRORS as e:
        raise ResearchError(f"Brave Search failed: {e or type(e).__name__}") from e


async def _read(client: httpx.AsyncClient, results: list[SearchResult]) -> dict[str, PageRead]:
    """Read the results' pages, keeping the first N_PAGES readable ones in rank order."""
    reads = await asyncio.gather(*(fetch_page(client, r.url) for r in results))
    pages, taken = {}, 0
    for page in reads:  # rank order
        if page.ok and taken >= N_PAGES:
            page.skipped = True
        elif page.ok:
            taken += 1
        pages[page.url] = page
    return pages


async def gather(
    query: str, api_key: str | None, language: str,
    client: httpx.AsyncClient | None = None,
) -> Research:
    """Search, then read the top results until N_PAGES pages are read."""
    if not api_key:
        raise ResearchError(f"No Brave Search key in the vault ({SEARCH_SECRET})")
    country, search_lang = locale_for(language)
    research = Research(query, language, country, search_lang, datetime.now().astimezone())
    own = client is None
    client = client or httpx.AsyncClient()
    try:
        research.results = await _search(client, query, api_key, country, search_lang)
        if not research.results:
            raise ResearchError(f"No search results for: {query}")
        research.pages = await _read(client, research.results)
    finally:
        if own:
            await client.aclose()
    return research


def _url_key(url: str) -> str:
    """URL compared for duplicates: no fragment, no trailing slash."""
    return url.split("#", 1)[0].rstrip("/")


async def extend(
    research: Research, query: str, api_key: str | None,
    client: httpx.AsyncClient | None = None,
    locale: tuple[str, str] | None = None,
) -> Research:
    """A copy of `research` with a second search's new results appended.

    Results whose URL is already there are dropped; the new ones are numbered
    after the existing sources, so citations of the first round stay valid.
    Up to N_PAGES of the new pages are read. `locale` (country, search_lang)
    is where to search, the first search's by default; the copy keeps the
    first search's language and locale either way. Raises ResearchError when
    the search fails.
    """
    if not api_key:
        raise ResearchError(f"No Brave Search key in the vault ({SEARCH_SECRET})")
    known = {_url_key(r.url) for r in research.results}
    own = client is None
    client = client or httpx.AsyncClient()
    try:
        country, search_lang = locale or (research.country, research.search_lang)
        found = await _search(client, query, api_key, country, search_lang)
        new = []
        for result in found:
            if _url_key(result.url) not in known:
                known.add(_url_key(result.url))
                new.append(replace(result, rank=len(research.results) + len(new) + 1))
        pages = await _read(client, new) if new else {}
    finally:
        if own:
            await client.aclose()
    return replace(research, results=[*research.results, *new],
                   pages={**research.pages, **pages})


# --- query ------------------------------------------------------------------

def parse_query(answer: str, fallback: str) -> str:
    line = next((ln for ln in answer.strip().splitlines() if ln.strip()), "")
    line = re.sub(r"^(query|search query)\s*:\s*", "", line.strip(), flags=re.I)
    query = line.strip().strip("\"'`“”*").strip()
    return query[:MAX_QUERY_CHARS] if query else fallback


def fallback_query(request: str) -> str:
    return " ".join(request.split()[:8])[:MAX_QUERY_CHARS]


async def derive_query(engine, request: str, language: str, title: str | None = None) -> str:
    """A short search query for the request (the LLM's, or its first words)."""
    subject = prompts.render("research.query_subject", title=title).strip() + "\n\n" if title else ""
    prompt = prompts.render("research.query", language=language, subject=subject, request=request.strip())
    return parse_query(await call_llm(engine, prompt), fallback_query(title or request))


# --- brief ------------------------------------------------------------------

def _source_block(n: int, result: SearchResult, research: Research) -> str:
    page = research.pages.get(result.url)
    if page is not None and page.ok:
        status, body = "read", page.text
    else:
        status, body = "snippet", result.snippet or "(no snippet)"
    trusted = ' trusted="yes"' if is_trusted(result.url) else ""
    return (
        f'<source n="{n}" status="{status}"{trusted} url="{result.url}">\n'
        f"# {result.title}\n\n{body}\n</source>"
    )


def _sources_xml(research: Research) -> str:
    return "\n\n".join(_source_block(r.rank, r, research) for r in research.results)


def _followup_note(research: Research) -> str:
    f = research.followup
    if f is None or not f.first_rank:
        return ""
    return prompts.render(
        "research.brief_followup", first=f.first_rank, last=len(research.results),
        query=f.query, reason=f.reason or "the first results did not settle the request.",
    ).strip()


def build_brief_prompt(research: Research, request: str, original_title: str | None = None) -> str:
    original = (
        "\n" + prompts.render("research.brief_rewrite", title=original_title).strip() + "\n"
        if original_title else ""
    )
    note = _followup_note(research)
    prompt = prompts.render(
        "research.brief",
        request=request.strip(),
        original=original,
        n=len(research.first_round),
        query=research.query,
        date=research.fetched_at.strftime("%Y-%m-%d"),
        sources=_sources_xml(research),
        language=research.language,
        outline_note=" " + prompts.text("research.brief_outline_note").strip() if original_title else "",
        followup="\n\n" + note if note else "",
    )
    # A saved override written before {followup} existed still gets the note
    if note and note not in prompt:
        prompt += "\n\n" + note
    return prompt


async def write_brief(
    research: Research, request: str, engine, original_title: str | None = None,
) -> str:
    text = await call_llm(engine, build_brief_prompt(research, request, original_title),
                          system=prompts.text("research.brief_system"))
    if not text or not text.strip():
        raise ValueError(f"{engine.id} returned an empty brief")
    return text.strip()


# --- follow-up search -------------------------------------------------------

def parse_followup(answer: str) -> tuple[str, str, str]:
    """(query, uncertainty, language code) from the decision; all "" when none is needed.

    Only a "Query:" line counts, so an answer in another shape (a brief, a
    refusal, NONE) never turns into a search. The "Language:" line is
    optional; it is returned as written and validated by locale_for_code().
    """
    query = reason = language = ""
    for line in (answer or "").splitlines():
        line = line.strip().lstrip("-*# ").strip()
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip().strip("*_").strip().lower()
        value = value.strip(" \t\"'`“”*_")
        if key in ("query", "search query", "follow-up query") and not query:
            query = value
        elif key == "uncertainty" and not reason:
            reason = value
        elif key in ("language", "language code") and not language:
            language = value
    if query.upper() == "NONE":
        return "", "", ""
    return query[:MAX_QUERY_CHARS], reason, language


def _same_query(a: str, b: str) -> bool:
    def norm(q: str) -> str:
        return " ".join(re.sub(r"[^\w\s]", " ", q.casefold()).split())
    return norm(a) == norm(b)


async def decide_followup(engine, research: Research, request: str, brief: str) -> tuple[str, str, str]:
    """(query, uncertainty, language code) for one more search; all "" when the first round suffices.

    It reads the request, the sources and the brief as plain text, so it works
    whatever sections a site's brief prompt asks for.
    """
    prompt = prompts.render(
        "research.followup", request=request.strip(), query=research.query,
        sources=_sources_xml(research), brief=brief.strip(), language=research.language,
    )
    return parse_followup(await call_llm(engine, prompt, system=prompts.text("research.followup_system")))


async def follow_up(
    research: Research, brief: str, request: str, engine, api_key: str | None,
    original_title: str | None = None, client: httpx.AsyncClient | None = None,
    status: Callable[[str], None] | None = None,
) -> tuple[Research, str]:
    """At most one more search when the first brief leaves a material question open.

    Returns the research and brief to review: both rounds and a rewritten
    brief when the extra search added sources, the first round otherwise.
    Never raises for the follow-up's own failures; research.followup records
    the queries' outcome for the review screen and the log.
    """
    say = status or (lambda _text: None)
    followup = FollowUp()

    def keep_first(outcome: str) -> tuple[Research, str]:
        followup.outcome = outcome
        return replace(research, followup=followup), brief

    say("Checking whether the sources settle the request…")
    try:
        followup.query, followup.reason, followup.language = await decide_followup(
            engine, research, request, brief)
    except LLM_ERRORS as e:
        log_exception("research: follow-up decision")
        return keep_first(f"not run: the decision failed ({e or type(e).__name__})")
    if not followup.query:
        return keep_first("not needed: the first round answers the request")
    if _same_query(followup.query, research.query):
        return keep_first("skipped: same as the first query")

    # Its own language only when the decision names one Brave supports; the
    # brief and the post keep research.language either way
    locale = locale_for_code(followup.language) if followup.language else None
    followup.country, followup.search_lang = locale or (research.country, research.search_lang)
    say(f"Follow-up search ({followup.country}/{followup.search_lang}): {followup.query}")
    try:
        merged = await extend(research, followup.query, api_key, client,
                              (followup.country, followup.search_lang))
    except ResearchError as e:
        return keep_first(f"failed: {e}; kept the first-round brief")
    added = merged.results[len(research.results):]
    if not added:
        return keep_first("no new sources; kept the first-round brief")
    followup.first_rank = added[0].rank
    merged.followup = followup
    read = sum(1 for r in added if merged.status(r) == "read")
    span = f"[{added[0].rank}]" if len(added) == 1 else f"[{added[0].rank}]–[{added[-1].rank}]"

    say(f"Read {read} of {len(added)} new pages. {engine.id} is rewriting the brief…")
    try:
        rewritten = await write_brief(merged, request, engine, original_title)
    except LLM_ERRORS as e:
        log_exception("research: brief after follow-up")
        followup.first_rank = 0
        return keep_first(f"found {len(added)} new sources, but the brief rewrite failed "
                          f"({e or type(e).__name__}); kept the first-round brief")
    followup.outcome = f"added {len(added)} sources {span}, {read} read; brief rewritten from both rounds"
    return merged, rewritten


def unknown_citations(brief: str, research: Research) -> list[int]:
    """Source numbers the brief cites that do not exist."""
    known = {r.rank for r in research.results}
    cited = {int(n) for group in re.findall(r"\[(\d+(?:\s*,\s*\d+)*)\]", brief)
             for n in re.split(r"\s*,\s*", group)}
    return sorted(cited - known)


def sources_markdown(research: Research) -> str:
    """Numbered source list built from the collected data, not from the LLM."""
    return "\n".join(
        f"[{r.rank}] {r.title} — {r.url} ({research.status(r)})"
        for r in research.results
    )


def writer_context(brief: str, research: Research) -> str:
    """The approved brief plus the source list, as the writer receives it."""
    return f"{brief.strip()}\n\n## Sources\n\n{sources_markdown(research)}"


def _followup_lines(research: Research) -> str:
    f = research.followup
    if f is None:
        return ""
    region = ""
    if f.country:
        region = f"- Follow-up region: {f.country}, language {f.search_lang}"
        if f.language and locale_for_code(f.language) is None:
            region += f" (asked for \"{f.language}\": not supported, used the first search's)"
        elif f.language:
            region += f" (asked for {f.language})"
        region += "\n"
    return (
        f"- Follow-up query: {f.query or '(none)'}\n"
        + (f"- Follow-up reason: {f.reason}\n" if f.reason else "")
        + region
        + f"- Follow-up search: {f.outcome}\n"
    )


def save_log(research: Research, brief: str, request: str, draft: Path) -> Path:
    """Keep the research behind a draft in ~/.hugin/research/<draft name>.md."""
    path = RESEARCH_DIR / f"{draft.stem}.md"
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, (
        f"# Research for {draft.name}\n\n"
        f"- Draft: {draft}\n"
        f"- Query: {research.query}\n"
        f"{_followup_lines(research)}"
        f"- Region: {research.country}, language {research.search_lang}\n"
        f"- Collected: {research.fetched_at.isoformat(timespec='seconds')}\n"
        f"- Pages read: {research.pages_read} of {len(research.results)} results\n\n"
        f"## Request\n\n{request.strip()}\n\n"
        f"{brief.strip()}\n\n## Sources\n\n{sources_markdown(research)}\n"
    ))
    return path
