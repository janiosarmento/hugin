"""Client for echo.fulcrum.inc: writes a post in the author's voice.

Echo is stateless and takes writing samples inline: each sample goes in a
<post> tag in the user message, followed by the actual request. The API key
is resolved from Jano at call time, using the key name configured in
engines.toml (see engines.load_fulcrum_echo_secret).
"""

import asyncio
import random
import re
from datetime import datetime
from pathlib import Path

import httpx

from hugin.engines import load_fulcrum_echo_secret
from hugin.scanner import Post

ECHO_URL = "https://echo.fulcrum.inc/api/v1/chat/completions"
ECHO_TIMEOUT = 900  # seconds; Echo's own server-side limit

FALLBACK_MIN_TIMEOUT = 300  # long-form writing outlasts typical chat timeouts

N_RECENT = 3
N_SIMILAR = 4
N_RANDOM = 0  # set to 1 to add a random sample for stylistic variety
MIN_SAMPLES = N_RECENT + N_SIMILAR + N_RANDOM

# Refactor mode (rewriting an existing post): the sample mix changes, and the
# original goes along as its own block, never as a sample.
R_N_RECENT = 2
R_N_SIMILAR = 2  # closest to the prompt
R_N_LIKE_ORIGINAL = 2  # closest to the post being rewritten
R_MIN_SAMPLES = R_N_RECENT + R_N_SIMILAR + R_N_LIKE_ORIGINAL


WRITER_SYSTEM_PROMPT = """\
You are a blog author writing in the voice of the writing samples you are given.

Style guidance (avoid these habits where you can; they are tendencies to \
steer clear of, not hard bans):
- Avoid em-dashes (—). Use commas, periods, parentheses or a rewritten \
sentence instead.
- Avoid strings of very short, punchy sentences, the staccato rhythm typical \
of AI prose ("It works. It's fast. It's simple."). Prefer sentences of \
natural, varied length, and join related ideas into flowing sentences and \
paragraphs, as a human writer would.
"""


class EchoError(Exception):
    """Raised for any failure talking to Echo; message is user-facing."""


def _is_published(post: Post) -> bool:
    if post.metadata.get("draft"):
        return False
    return post.date is None or post.date <= datetime.now(post.date.tzinfo)


def published_posts(posts: list[Post]) -> list[Post]:
    return [p for p in posts if _is_published(p)]


def has_enough_samples(posts: list[Post], original: Post | None = None) -> bool:
    candidates = [p for p in published_posts(posts) if original is None or p.path != original.path]
    return len(candidates) >= (MIN_SAMPLES if original is None else R_MIN_SAMPLES)


def original_query(original: Post) -> str:
    """Text used to find the posts closest to `original`."""
    return f"{original.metadata.get('title', '')}\n\n{original.content}"


def _select_parts(
    posts: list[Post], original: Post | None = None
) -> tuple[list[Post], list[Post]]:
    """(most recent, every other candidate), published posts only.

    `original` (refactor mode) is never a candidate.
    """
    published = [
        p for p in published_posts(posts) if original is None or p.path != original.path
    ]
    recent = sorted(
        (p for p in published if p.date is not None),
        key=lambda p: p.date.replace(tzinfo=None),
        reverse=True,
    )[: N_RECENT if original is None else R_N_RECENT]
    taken = {p.path for p in recent}
    return recent, [p for p in published if p.path not in taken]


def draw_random(rest: list[Post], rng: random.Random | None = None) -> Post | None:
    """The random sample, or None when N_RANDOM is 0."""
    return (rng or random).choice(rest) if rest and N_RANDOM else None


def pick_similar(
    pool: list[Post],
    ranked_paths: list[str] | None,
    n: int | None = None,
) -> list[Post]:
    """Up to n (default N_SIMILAR) candidates, in the ranking's order."""
    n = N_SIMILAR if n is None else n
    by_abs = {str(p.path.resolve()): p for p in pool}
    out = []
    for path in ranked_paths or []:
        if path in by_abs:
            out.append(by_abs[path])
            if len(out) == n:
                break
    return out


def select_samples(
    posts: list[Post],
    ranked_paths: list[str] | None = None,
    rng: random.Random | None = None,
    random_pick: Post | None = None,
) -> list[Post]:
    """N_RECENT most recent + N_SIMILAR closest to the prompt + N_RANDOM random.

    `ranked_paths` is the semantic ranking (absolute paths, best first) of
    the user's prompt against the blog. `random_pick` pins the random sample
    (so a UI can show it before sending); it is drawn here when not given.
    Without a ranking (embeddings unavailable) random posts stand in for
    the missing similar ones. With fewer than MIN_SAMPLES published posts
    the result is shorter; callers should check has_enough_samples() first.
    """
    rng = rng or random
    recent, rest = _select_parts(posts)
    if not N_RANDOM:
        random_pick = None
    elif random_pick is None or random_pick.path not in {p.path for p in rest}:
        random_pick = draw_random(rest, rng)
    pool = [p for p in rest if random_pick is None or p.path != random_pick.path]
    similar = pick_similar(pool, ranked_paths)
    if len(similar) < N_SIMILAR:
        left = [p for p in pool if p.path not in {s.path for s in similar}]
        similar += rng.sample(left, min(N_SIMILAR - len(similar), len(left)))
    return recent + similar + ([random_pick] if random_pick else [])


def select_refactor_similar(
    pool: list[Post],
    ranked_prompt: list[str] | None,
    ranked_original: list[str] | None,
    rng: random.Random | None = None,
) -> tuple[list[Post], list[Post]]:
    """(closest to the prompt, closest to the original), no overlap.

    Each list is topped up randomly when its ranking is unavailable.
    """
    rng = rng or random
    similar = pick_similar(pool, ranked_prompt, R_N_SIMILAR)
    left = [p for p in pool if p.path not in {s.path for s in similar}]
    if len(similar) < R_N_SIMILAR:
        similar += rng.sample(left, min(R_N_SIMILAR - len(similar), len(left)))
        left = [p for p in left if p.path not in {s.path for s in similar}]
    like_original = pick_similar(left, ranked_original, R_N_LIKE_ORIGINAL)
    if len(like_original) < R_N_LIKE_ORIGINAL:
        rest = [p for p in left if p.path not in {s.path for s in like_original}]
        like_original += rng.sample(rest, min(R_N_LIKE_ORIGINAL - len(like_original), len(rest)))
    return similar, like_original


def select_refactor_samples(
    posts: list[Post],
    original: Post,
    ranked_prompt: list[str] | None = None,
    ranked_original: list[str] | None = None,
    rng: random.Random | None = None,
) -> list[Post]:
    """2 most recent + 2 closest to the prompt + 2 closest to the original."""
    recent, rest = _select_parts(posts, original)
    similar, like_original = select_refactor_similar(rest, ranked_prompt, ranked_original, rng)
    return recent + similar + like_original


def build_message(samples: list[Post], request: str, original: Post | None = None) -> str:
    blocks = []
    for post in samples:
        title = post.metadata.get("title", post.path.stem)
        blocks.append(f"<post>\n# {title}\n\n{post.content.strip()}\n</post>")
    n = len(samples)
    if original is None:
        task = "Write a new post in my voice, following the request below. "
        extra = ""
    else:
        title = original.metadata.get("title", original.path.stem)
        extra = (
            "\n\nHere is the post to rewrite:\n\n"
            f"<original>\n# {title}\n\n{original.content.strip()}\n</original>"
        )
        words = len(original.content.split())
        task = (
            "This is a REFACTOR, not a new post and not a follow-up. The new "
            "version will REPLACE the original on my blog, so a reader who "
            "only sees it must get everything the original gave them.\n\n"
            "How to do it:\n"
            "1. Edit, do not summarize. Start from the original text and "
            "revise it. Keep its structure, sections, anecdotes, jokes, "
            "details and explanations, and keep every markdown image and "
            "link exactly as written (same URLs). Rewrite only what the "
            "request makes necessary.\n"
            f"2. Length: the original has about {words} words. The new post "
            f"must have at least {int(words * 0.9)}, and more if the request "
            "adds material. A shorter result is a failure.\n"
            "3. Apply the request inside the story, not after it. If it "
            "brings new facts or a different outcome, rework the post "
            "around them: change the angle, the verdict and the conclusion, "
            "and fix every statement the new facts contradict (praise, "
            "recommendations, plans), so the post never contradicts itself. "
            "Never tack the news on as an epilogue or a 'later I found out'.\n"
            "4. It is a single standalone post. Do not mention an earlier "
            "post or version, and do not present it as an update, a sequel "
            "or a second part (not in the text and not in the title). The "
            "title may change to fit the new angle.\n"
            "5. The other posts above are only samples of my voice; do not "
            "borrow their content.\n\n"
            "The request below says what to change. "
        )
    return (
        f"Here are {n} of my blog posts:\n\n"
        + "\n\n".join(blocks)
        + extra
        + f"\n\n{task}"
        "Put the post title alone on the first line (plain text, no # or "
        "other markup, no quotes), then a blank line, then the post body."
        f"\n\nRequest:\n\n{request.strip()}"
    )


def get_api_key() -> str:
    from secrets_resolver import get_secret
    from secrets_resolver.exceptions import SecretsResolverError

    name = load_fulcrum_echo_secret()
    try:
        key = get_secret(name)
    except SecretsResolverError as e:
        raise EchoError(f"Echo API key not found. Run: set-secret {name}") from e
    if not key:
        raise EchoError(f"Echo API key is empty. Run: set-secret {name}")
    return key


def _error_detail(response: httpx.Response) -> str:
    """Short server-side reason (e.g. 'insufficient credits'), if the body has one."""
    try:
        err = response.json().get("error")
    except Exception:
        return ""
    if isinstance(err, dict):
        err = err.get("message")
    return f": {str(err)[:200]}" if err else ""


async def ask_echo(message: str, persona: str, api_key: str) -> str:
    payload = {
        "model": "echo",
        "persona": persona,
        # Echo has no system field, so the style guidance rides along in the message
        "messages": [{"role": "user", "content": f"{message}\n\n{WRITER_SYSTEM_PROMPT}"}],
    }
    headers = {"Authorization": f"Bearer {api_key}", "User-Agent": "hugin/0.1"}
    try:
        async with httpx.AsyncClient(timeout=ECHO_TIMEOUT) as client:
            response = await client.post(ECHO_URL, json=payload, headers=headers)
    except httpx.TimeoutException as e:
        raise EchoError("Echo did not answer in time") from e
    except httpx.HTTPError as e:
        raise EchoError(f"Could not reach Echo: {e}") from e
    if response.status_code == 401:
        raise EchoError("Echo rejected the API key (401) — rotate it in Jano?")
    if response.status_code >= 400:
        raise EchoError(f"Echo returned HTTP {response.status_code}{_error_detail(response)}")
    try:
        text = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise EchoError("Unexpected response format from Echo") from e
    if not text or not text.strip():
        raise EchoError("Echo returned an empty answer")
    return text.strip()


async def write_with_system_llm(message: str, engine) -> str:
    """Have the system LLM (the engine picked with `n`) write the post."""
    if engine is None or not engine.available:
        raise EchoError("No usable system LLM (check the selected engine and its key)")

    from dataclasses import replace

    from hugin.llm import call_llm

    patient = replace(engine, timeout=max(engine.timeout, FALLBACK_MIN_TIMEOUT))
    try:
        text = await call_llm(patient, message, system=WRITER_SYSTEM_PROMPT)
    except Exception as e:
        raise EchoError(f"{engine.id} failed: {e}") from e
    if not text or not text.strip():
        raise EchoError(f"{engine.id} returned nothing")
    return text.strip()


async def write_with_fallback(
    message: str, persona: str, engine
) -> tuple[str, str | None]:
    """Ask Echo; if it fails for any reason, delegate to the system LLM.

    Returns (text, echo_error): echo_error is None when Echo answered, else
    the reason Echo failed (the text then comes from `engine`). Raises
    EchoError only if the fallback is also impossible or fails too.
    """
    try:
        api_key = await asyncio.to_thread(get_api_key)
        return await ask_echo(message, persona, api_key), None
    except EchoError as echo_error:
        reason = str(echo_error)
    if engine is None or not engine.available:
        raise EchoError(f"{reason} (and no system LLM available as fallback)")
    try:
        return await write_with_system_llm(message, engine), reason
    except EchoError as e:
        raise EchoError(f"{reason}; fallback failed too: {e}") from e


MAX_TITLE_CHARS = 120


def split_title(text: str, fallback: str) -> tuple[str, str]:
    """Take the first line of Echo's answer as the title, the rest as the body.

    Tolerates stray markup on the title line (leading '#', '**', quotes). If
    the first line is too long to be a title, the whole text is the body and
    `fallback` is the title.
    """
    first, _, rest = text.strip().partition("\n")
    title = first.strip().lstrip("#").strip().strip("*_\"'“”").strip()
    if not title or len(title) > MAX_TITLE_CHARS:
        return fallback, text.strip()
    return title, rest.strip()


def parse_answer(text: str, request: str) -> tuple[str, str]:
    """Split Echo's answer into (title, body); title is quote-safe."""
    fallback = " ".join(request.split()[:8])
    title, body = split_title(text, fallback)
    return title.replace('"', "'"), body


CATEGORY_PROMPT = """\
Pick the single best category for the blog post below.

Available categories:
{categories}

Post title: {title}

Post text:
{body}

Answer with the category name only, exactly as written in the list above."""

CATEGORY_BODY_CHARS = 6000


def parse_category(response: str, categories: list[str]) -> str | None:
    """Match the LLM answer against the known categories (case-insensitive)."""
    answer = response.strip().splitlines()[0].strip().strip("-*\"'`. ") if response.strip() else ""
    by_lower = {c.lower(): c for c in categories}
    if answer.lower() in by_lower:
        return by_lower[answer.lower()]
    # Tolerate a short sentence around the name, e.g. "Category: Cats".
    for lower, original in by_lower.items():
        if lower in response.lower():
            return original
    return None


async def pick_category(engine, title: str, body: str, categories: list[str]) -> str | None:
    """Ask the system LLM (not Echo: cheaper) for the post's category.

    Returns None when there are no categories or the answer matches none.
    Falls back to the first category on LLM failure, since a TBD category
    makes PagesCMS refuse to save the post.
    """
    if not categories:
        return None
    from hugin.llm import call_llm

    prompt = CATEGORY_PROMPT.format(
        categories="\n".join(f"- {c}" for c in categories),
        title=title,
        body=body[:CATEGORY_BODY_CHARS],
    )
    try:
        answer = await call_llm(engine, prompt)
    except Exception:
        return categories[0]
    return parse_category(answer, categories) or categories[0]


def create_draft(
    directory: Path,
    text: str,
    request: str,
    category: str | None = None,
    refactor_of: str | None = None,
) -> Path:
    from hugin.hugo import slugify
    from hugin.writer import create_post

    title, body = parse_answer(text, request)
    base = slugify(title) or "echo-draft"
    slug, n = base, 1
    while (directory / f"{slug}.md").exists():
        slug = f"{base}-{n}"
        n += 1
    path = directory / f"{slug}.md"
    create_post(path, title=title, slug=slug, category=category, body=body,
                prompt=request, refactor_of=refactor_of)
    return path
