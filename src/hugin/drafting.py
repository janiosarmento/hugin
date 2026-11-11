"""Drafts written by the system LLM in the author's voice (no UI).

Writing samples go inline: each sample goes in a <post> tag in the user
message, followed by the actual request.
"""

import random
from datetime import datetime
from pathlib import Path

import httpx

from hugin.authors import post_authors
from hugin.log import log_exception
from hugin.scanner import Post
from dataclasses import replace
from hugin.llm import call_llm
from hugin.hugo import slugify
from hugin.writer import create_post

MIN_WRITER_TIMEOUT = 300  # long-form writing outlasts typical chat timeouts

N_RECENT = 3
N_SIMILAR = 4
N_RANDOM = 0  # set to 1 to add a random sample for stylistic variety
MIN_SAMPLES = N_RECENT + N_SIMILAR + N_RANDOM

# Target length for a new post, asked for in the form (words). The model won't
# hit it exactly, so this is a direction, not a limit.
DEFAULT_TARGET_WORDS = 800
MIN_TARGET_WORDS = 50
MAX_TARGET_WORDS = 5000

# Refactor mode (rewriting an existing post): the sample mix changes, and the
# original goes along as its own block, never as a sample.
R_N_RECENT = 2
R_N_SIMILAR = 2  # closest to the prompt
R_N_LIKE_ORIGINAL = 2  # closest to the post being rewritten
R_MIN_SAMPLES = R_N_RECENT + R_N_SIMILAR + R_N_LIKE_ORIGINAL


WRITER_SYSTEM_PROMPT = (
    "You are a blog author. Write in the voice of the writing samples and author profile you are given."
)


# What a failed LLM call can raise: network/HTTP problems, a repetition loop
# or malformed JSON (ValueError), and an unexpected response shape. Anything
# else is a bug and must not be swallowed.
LLM_ERRORS = (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError)

# What the embedding index can raise when the model or its files are unusable
EMBEDDING_ERRORS = (OSError, RuntimeError, ValueError, ImportError)


class DraftError(Exception):
    """Raised when a draft cannot be written; message is user-facing."""


def _is_published(post: Post) -> bool:
    if post.metadata.get("draft"):
        return False
    return post.date is None or post.date <= datetime.now(post.date.tzinfo)


def published_posts(posts: list[Post]) -> list[Post]:
    return [p for p in posts if _is_published(p)]


def _candidates(
    posts: list[Post], original: Post | None = None, author: str | None = None
) -> list[Post]:
    """Published posts usable as samples: never `original`, only `author`'s when given."""
    return [
        p for p in published_posts(posts)
        if (original is None or p.path != original.path)
        and (author is None or author in post_authors(p))
    ]


def has_enough_samples(
    posts: list[Post], original: Post | None = None, author: str | None = None
) -> bool:
    candidates = _candidates(posts, original, author)
    return len(candidates) >= (MIN_SAMPLES if original is None else R_MIN_SAMPLES)


def original_query(original: Post) -> str:
    """Text used to find the posts closest to `original`."""
    return f"{original.metadata.get('title', '')}\n\n{original.content}"


def inherited_fields(original: Post) -> dict[str, str | None]:
    """Fields a refactor takes from the post it replaces.

    Keys are create_draft keyword names. Placeholders ("TBD") and empty
    values count as missing (None).
    """
    def real(value) -> str | None:
        value = str(value).strip() if value else ""
        return value if value and value != "TBD" else None

    categories = original.metadata.get("categories")
    first = categories[0] if isinstance(categories, list) and categories else categories
    return {
        "category": real(first),
        "thumbnail": real(original.metadata.get("thumbnail")),
        "translation_key": real(original.metadata.get("translationKey")),
    }


def _select_parts(
    posts: list[Post], original: Post | None = None, author: str | None = None
) -> tuple[list[Post], list[Post]]:
    """(most recent, every other candidate), see _candidates."""
    published = _candidates(posts, original, author)
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
    author: str | None = None,
) -> list[Post]:
    """N_RECENT most recent + N_SIMILAR closest to the prompt + N_RANDOM random.

    `ranked_paths` is the semantic ranking (absolute paths, best first) of
    the user's prompt against the blog. `random_pick` pins the random sample
    (so a UI can show it before sending); it is drawn here when not given.
    Without a ranking (embeddings unavailable) random posts stand in for
    the missing similar ones. With fewer than MIN_SAMPLES published posts
    the result is shorter; callers should check has_enough_samples() first.
    `author` keeps only that author's posts.
    """
    rng = rng or random
    recent, rest = _select_parts(posts, author=author)
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
    author: str | None = None,
) -> list[Post]:
    """2 most recent + 2 closest to the prompt + 2 closest to the original."""
    recent, rest = _select_parts(posts, original, author)
    similar, like_original = select_refactor_similar(rest, ranked_prompt, ranked_original, rng)
    return recent + similar + like_original


def build_message(
    samples: list[Post], request: str, original: Post | None = None,
    constraints: str | None = None, words: int | None = None,
    profile: str | None = None, research: str | None = None,
) -> str:
    blocks = []
    for post in samples:
        title = post.metadata.get("title", post.path.stem)
        blocks.append(f"<post>\n# {title}\n\n{post.content.strip()}\n</post>")
    n = len(samples)
    intro = [f"Here are {n} of my blog posts:\n\n" + "\n\n".join(blocks)] if samples else []
    if profile and profile.strip():
        intro.append(f"Author profile (the voice and style to write in):\n\n{profile.strip()}")
    if original is None:
        task = "Write a new post in my voice, following the request below. "
        if words:
            task += f"Target length: about {words} words. "
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
    if research and research.strip():
        extra += (
            "\n\nResearch brief for this post (made from a web search and "
            "reviewed by me; the numbers in brackets point to the sources "
            "listed at its end):\n\n"
            f"<research>\n{research.strip()}\n</research>\n\n"
            "Use the brief to decide what to cover and for facts. Take facts "
            "only from it and link their source URL inline (Markdown) where it "
            "reads naturally; never copy sentences from the sources. Keep my "
            "voice from the samples, not the tone of the sources. Facts marked "
            "\"(snippet)\" come from a search snippet only: hedge them or leave "
            "them out."
            + (" In this refactor the brief's outline is only a suggestion; the "
               "refactor rules below come first." if original is not None else "")
        )
    rules = (
        f"\n\nGuidelines:\n\n{constraints.strip()}"
        if constraints and constraints.strip() else ""
    )
    return (
        "\n\n".join(intro)
        + extra
        + f"\n\n{task}"
        "Put the post title alone on the first line (plain text, no # or "
        "other markup, no quotes), then a blank line, then the post body."
        + rules
        + f"\n\nRequest:\n\n{request.strip()}"
    )


async def write_draft(message: str, engine) -> str:
    """Have the system LLM (the engine picked with `n`) write the post."""
    if engine is None or not engine.available:
        raise DraftError("No usable system LLM (check the selected engine and its key)")

    patient = replace(engine, timeout=max(engine.timeout, MIN_WRITER_TIMEOUT))
    try:
        text = await call_llm(patient, message, system=WRITER_SYSTEM_PROMPT)
    except LLM_ERRORS as e:
        raise DraftError(f"{engine.id} failed: {e}") from e
    if not text or not text.strip():
        raise DraftError(f"{engine.id} returned nothing")
    return text.strip()


MAX_TITLE_CHARS = 120


def split_title(text: str, fallback: str) -> tuple[str, str]:
    """Take the first line of the answer as the title, the rest as the body.

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
    """Split the LLM answer into (title, body); title is quote-safe."""
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
    """Ask the system LLM for the post's category.

    Returns None when there are no categories or the answer matches none.
    Falls back to the first category on LLM failure, since a TBD category
    makes PagesCMS refuse to save the post.
    """
    if not categories:
        return None

    prompt = CATEGORY_PROMPT.format(
        categories="\n".join(f"- {c}" for c in categories),
        title=title,
        body=body[:CATEGORY_BODY_CHARS],
    )
    try:
        answer = await call_llm(engine, prompt)
    except LLM_ERRORS:
        log_exception("pick category")
        return categories[0]
    return parse_category(answer, categories) or categories[0]


def create_draft(
    directory: Path,
    text: str,
    request: str,
    category: str | None = None,
    refactor_of: str | None = None,
    thumbnail: str | None = None,
    translation_key: str | None = None,
    author: str | None = None,
    author_field: str = "author",
) -> Path:

    title, body = parse_answer(text, request)
    base = slugify(title) or "draft"
    slug, n = base, 1
    while (directory / f"{slug}.md").exists():
        slug = f"{base}-{n}"
        n += 1
    path = directory / f"{slug}.md"
    create_post(path, title=title, slug=slug, category=category, body=body,
                prompt=request, refactor_of=refactor_of, thumbnail=thumbnail,
                translation_key=translation_key, author=author, author_field=author_field)
    return path
