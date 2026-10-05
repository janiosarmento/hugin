"""Client for echo.fulcrum.inc: writes a post in the author's voice.

Echo is stateless and takes writing samples inline: each sample goes in a
<post> tag in the user message, followed by the actual request. The API key
is resolved from Jano at call time, using the key name configured in
engines.toml (see engines.load_fulcrum_echo_secret).
"""

import random
import re
from datetime import datetime
from pathlib import Path

import httpx

from hugin.engines import load_fulcrum_echo_secret
from hugin.scanner import Post

ECHO_URL = "https://echo.fulcrum.inc/api/v1/chat/completions"
ECHO_TIMEOUT = 900  # seconds; Echo's own server-side limit

N_RECENT = 3
N_LARGEST = 2
N_SIMILAR = 1
MIN_SAMPLES = N_RECENT + N_LARGEST + N_SIMILAR


class EchoError(Exception):
    """Raised for any failure talking to Echo; message is user-facing."""


def _is_published(post: Post) -> bool:
    if post.metadata.get("draft"):
        return False
    return post.date is None or post.date <= datetime.now(post.date.tzinfo)


def published_posts(posts: list[Post]) -> list[Post]:
    return [p for p in posts if _is_published(p)]


def has_enough_samples(posts: list[Post]) -> bool:
    return len(published_posts(posts)) >= MIN_SAMPLES


def select_samples(
    posts: list[Post],
    ranked_paths: list[str] | None = None,
    rng: random.Random | None = None,
) -> list[Post]:
    """3 most recent published posts, the 2 largest of the rest, and 1 similar.

    `ranked_paths` is the semantic ranking (absolute paths, best first) of
    the user's prompt against the blog; the first not-yet-chosen published
    post in it is the "similar" sample. Without a ranking (embeddings
    unavailable) a random other post stands in. All picks are distinct. With
    fewer than MIN_SAMPLES published posts the result is shorter; callers
    should check has_enough_samples() first.
    """
    rng = rng or random
    published = published_posts(posts)
    recent = sorted(
        (p for p in published if p.date is not None),
        key=lambda p: p.date.replace(tzinfo=None),
        reverse=True,
    )[:N_RECENT]
    taken = {p.path for p in recent}
    rest = [p for p in published if p.path not in taken]
    largest = sorted(rest, key=lambda p: p.path.stat().st_size, reverse=True)[:N_LARGEST]
    taken |= {p.path for p in largest}
    others = [p for p in rest if p.path not in taken]

    extra: list[Post] = []
    by_abs = {str(p.path.resolve()): p for p in others}
    for path in ranked_paths or []:
        if path in by_abs:
            extra = [by_abs[path]]
            break
    if not extra:
        extra = rng.sample(others, min(N_SIMILAR, len(others)))
    return recent + largest + extra


def build_message(samples: list[Post], request: str) -> str:
    blocks = []
    for post in samples:
        title = post.metadata.get("title", post.path.stem)
        blocks.append(f"<post>\n# {title}\n\n{post.content.strip()}\n</post>")
    n = len(samples)
    return (
        f"Here are {n} of my blog posts:\n\n"
        + "\n\n".join(blocks)
        + "\n\nWrite a new post in my voice, following the request below. "
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


async def ask_echo(message: str, persona: str, api_key: str) -> str:
    payload = {
        "model": "echo",
        "persona": persona,
        "messages": [{"role": "user", "content": message}],
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
        raise EchoError(f"Echo returned HTTP {response.status_code}")
    try:
        text = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise EchoError("Unexpected response format from Echo") from e
    if not text or not text.strip():
        raise EchoError("Echo returned an empty answer")
    return text.strip()


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
    directory: Path, text: str, request: str, category: str | None = None
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
    create_post(path, title=title, slug=slug, category=category, body=body)
    return path
