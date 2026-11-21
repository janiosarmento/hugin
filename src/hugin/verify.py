"""Checks of a written draft against what it was written from (`h`, `R`).

Two checks, shown before the draft is saved:

- links (no LLM): every external link in the draft must come from the
  original post or from the sources of the research; any other URL is
  reported, since a plausible-looking invented URL is a common LLM error;
- statements (a separate LLM call): factual statements none of the
  references support, statements against an approved fact-check decision,
  facts attributed to a source whose text does not contain them, and
  experiences of the author that neither the request nor the original
  states. A post with no references gets only that last check.

The second check is itself an LLM and can miss things: it narrows what the
author has to reread, it does not replace that reading.

What the checks find is fixed before the author sees it (`check_and_fix`):
unknown links become their plain text, and a third LLM call rewrites each
problem passage from the references (correct it, make it less specific, or
drop it). Only what could not be fixed is left for the author.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from hugin import factcheck as fc
from hugin import prompts
from hugin import research as web
from hugin.drafting import LLM_ERRORS, MD_LINK, MD_TARGET, MIN_WRITER_TIMEOUT
from hugin.llm import call_llm

ORIGINAL_CHARS = 30000
SOURCE_CHARS = web.PAGE_CHARS

KINDS = {
    "unsupported": "unsupported claim",
    "decision": "against a decision",
    "attribution": "wrong attribution",
    "personal": "invented experience",
}


def _norm(url: str) -> str:
    """URL compared without fragment, trailing slash or http/https difference."""
    url = url.split("#", 1)[0].rstrip("/")
    return url.removeprefix("https://").removeprefix("http://").removeprefix("www.")


def external_links(body: str) -> dict[str, str]:
    """External link URL -> text of its first link (images left out)."""
    out: dict[str, str] = {}
    for bang, text, url in MD_TARGET.findall(body):
        if not bang and url.startswith(("http://", "https://")):
            out.setdefault(url, " ".join(text.split()))
    return out


def unknown_links(draft: str, allowed: set[str]) -> list[tuple[str, str]]:
    """External links of the draft that are in neither the original nor the sources."""
    known = {_norm(u) for u in allowed}
    return [
        ("unknown link", url + (f"  “{text}”" if text else ""))
        for url, text in external_links(draft).items()
        if _norm(url) not in known
    ]


def strip_links(draft: str, allowed: set[str]) -> str:
    """`draft` with every link outside `allowed` turned into its text."""
    known = {_norm(u) for u in allowed}
    return MD_LINK.sub(
        lambda m: m.group(0) if _norm(m.group(2)) in known
        or not m.group(2).startswith(("http://", "https://")) else m.group(1),
        draft,
    )


def allowed_links(original: str | None, research: web.Research | None,
                  check: fc.FactCheck | None) -> set[str]:
    urls = set(external_links(original or ""))
    for found in (research, check.research if check else None):
        if found is not None:
            urls |= {r.url for r in found.results}
    return urls


def linked_sources(draft: str, research: web.Research | None) -> list[tuple[int, str, str, str]]:
    """(n, title, url, text) of the sources the draft links: page text when read."""
    if research is None:
        return []
    linked = {_norm(u) for u in external_links(draft)}
    out = []
    for r in research.results:
        if _norm(r.url) not in linked:
            continue
        page = research.pages.get(r.url)
        text = page.text if page is not None and page.ok else f"(search snippet only) {r.snippet}"
        out.append((r.rank, r.title, r.url, text[:SOURCE_CHARS]))
    return out


def _references(
    original: str | None = None, brief: str | None = None,
    decisions: str | None = None, sources: list[tuple[int, str, str, str]] | None = None,
    request: str | None = None,
) -> str:
    refs = []
    if request:
        refs.append(f"What the author asked the writer for:\n<request>\n{request.strip()}\n</request>")
    if original:
        refs.append(f"The post this draft rewrites:\n<original>\n{original[:ORIGINAL_CHARS]}\n</original>")
    if decisions:
        refs.append(
            prompts.text("verify.decisions").strip()
            + f"\n<decisions>\n{decisions}\n</decisions>"
        )
    if brief:
        refs.append(f"Research brief the author approved:\n<brief>\n{brief}\n</brief>")
    for n, title, url, text in sources or []:
        refs.append(f'<source n="{n}" url="{url}">\n# {title}\n\n{text}\n</source>')
    return "\n\n".join(refs)


def build_verify_prompt(
    draft: str, original: str | None = None, brief: str | None = None,
    decisions: str | None = None, sources: list[tuple[int, str, str, str]] | None = None,
    request: str | None = None, facts: bool = True,
) -> str:
    """With `facts` False only invented experiences are checked (no references to check facts)."""
    kinds = (["verify.fact_kinds"] if facts else []) + ["verify.personal_kind"]
    return prompts.render(
        "verify.prompt",
        references=_references(original, brief, decisions, sources, request), draft=draft,
        kinds="\n".join(prompts.text(k).strip() for k in kinds),
    )


@dataclass
class Issue:
    kind: str  # a KINDS label
    quote: str
    note: str = ""

    def row(self) -> tuple[str, str]:
        return (self.kind, f"“{self.quote}”" + (f" — {self.note}" if self.note else ""))


def parse_statement_issues(text: str) -> list[Issue]:
    issues = []
    for item in fc._json_array(text):
        if not isinstance(item, dict):
            continue
        quote = " ".join(str(item.get("quote") or "").split())
        if not quote:
            continue
        kind = KINDS.get(str(item.get("kind") or "").lower(), "unsupported claim")
        issues.append(Issue(kind, quote, " ".join(str(item.get("note") or "").split())))
    return issues


def parse_issues(text: str) -> list[tuple[str, str]]:
    return [i.row() for i in parse_statement_issues(text)]


def _patient(engine):
    return replace(engine, timeout=max(engine.timeout, MIN_WRITER_TIMEOUT))


async def check_statements(
    engine, draft: str, original: str | None = None, brief: str | None = None,
    decisions: str | None = None, sources=None, request: str | None = None,
) -> list[tuple[str, str]]:
    """The statement problems, or one "verification" row saying the check failed."""
    prompt = build_verify_prompt(draft, original, brief, decisions, sources, request)
    try:
        return parse_issues(await call_llm(_patient(engine), prompt, system=prompts.text("verify.system")))
    except LLM_ERRORS as e:
        return [_not_done(engine, e)]


def _not_done(engine, e: Exception) -> tuple[str, str]:
    return ("verification", f"not done: {engine.id} failed ({e}); check the facts yourself")


# --- fixing -----------------------------------------------------------------

def build_fix_prompt(draft: str, issues: list[Issue], references: str) -> str:
    problems = "\n".join(
        f"{i}. {issue.kind}: “{issue.quote}”" + (f" — {issue.note}" if issue.note else "")
        for i, issue in enumerate(issues, 1)
    )
    return prompts.render("verify.fix", references=references, problems=problems, draft=draft)


def _drop(draft: str, find: str) -> str:
    """`draft` without `find`, and without the space or blank line it leaves."""
    for gap in (f"{find} ", f" {find}", find):
        if gap in draft:
            draft = draft.replace(gap, "", 1)
            break
    return re.sub(r"\n{3,}", "\n\n", draft)


def apply_fixes(
    draft: str, issues: list[Issue], answer: str,
) -> tuple[str, list[tuple[str, str]], list[Issue]]:
    """(fixed draft, rows on what changed, issues still open).

    A fix whose "find" is not in the draft (as the engine copied it) is not
    applied, and the problems it was for stay open.
    """
    fixed: set[int] = set()
    rows = []
    for item in fc._json_array(answer):
        if not isinstance(item, dict):
            continue
        find = str(item.get("find") or "").strip()
        new = str(item.get("replace") or "").strip()
        ns = item.get("n")
        ns = [ns] if isinstance(ns, int) else [n for n in ns or [] if isinstance(n, int)]
        ns = [n for n in ns if 1 <= n <= len(issues) and n not in fixed]
        if not find or not ns or find == new or find not in draft:
            continue
        draft = draft.replace(find, new, 1) if new else _drop(draft, find)
        fixed.update(ns)
        note = " ".join(str(item.get("note") or "").split())
        short = " ".join(find.split())
        if new:
            rows.append(("fixed", f"“{short}” → “{' '.join(new.split())}”" + (f" — {note}" if note else "")))
        else:
            rows.append(("removed", f"“{short}”" + (f" — {note}" if note else "")))
    return draft, rows, [issue for i, issue in enumerate(issues, 1) if i not in fixed]


async def check_and_fix(
    engine, draft: str, original: str | None = None, brief: str | None = None,
    decisions: str | None = None, sources=None, status=None,
    request: str | None = None, facts: bool = True,
) -> tuple[str, list[tuple[str, str]], list[tuple[str, str]]]:
    """Check the statements of `draft` and fix what is found.

    Returns (draft, rows on what was fixed, rows still open). `status(text)`
    is told when the fixing starts. A failed check leaves one "verification"
    row; a failed fix leaves every problem open.
    """
    patient = _patient(engine)
    references = _references(original, brief, decisions, sources, request)
    try:
        issues = parse_statement_issues(await call_llm(
            patient, build_verify_prompt(draft, original, brief, decisions, sources, request, facts),
            system=prompts.text("verify.system"),
        ))
    except LLM_ERRORS as e:
        return draft, [], [_not_done(engine, e)]
    if not issues:
        return draft, [], []
    if status:
        status(f"{engine.id} is fixing {len(issues)} problem(s)…")
    try:
        answer = await call_llm(
            patient, build_fix_prompt(draft, issues, references), system=prompts.text("verify.fix_system"),
        )
        draft, rows, still = apply_fixes(draft, issues, answer)
    except LLM_ERRORS:
        return draft, [], [i.row() for i in issues]
    return draft, rows, [i.row() for i in still]
