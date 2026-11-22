"""Communication with the LLM endpoint and parsing of its responses."""

import ast
import json
import re

import httpx

from hugin import agy, prompts
from hugin.engines import TRANSPORT_AGY, Engine
from hugin.normalizer import detect_language
from collections import Counter

TOKEN_ESTIMATE_DIVISOR = 4
MAX_TOKENS_CONTENT = 4000


def _estimate_tokens(text: str) -> int:
    return len(text) // TOKEN_ESTIMATE_DIVISOR


def _truncate_post(metadata: dict, content: str) -> str:
    title = metadata.get("title", "")
    description = metadata.get("description", "")

    # Extrair headings
    headings = [line for line in content.splitlines() if line.startswith("#")]

    # First paragraphs (up to ~1000 chars)
    paragraphs = content[:1000]

    parts = []
    if title:
        parts.append(f"Title: {title}")
    if description:
        parts.append(f"Description: {description}")
    if headings:
        parts.append("Headings:\n" + "\n".join(headings))
    parts.append(f"Content (truncated):\n{paragraphs}")

    return "\n\n".join(parts)


def build_prompt(metadata: dict, content: str, pool_str: str) -> str:
    if _estimate_tokens(content) > MAX_TOKENS_CONTENT:
        content = _truncate_post(metadata, content)
    return prompts.render("tags.user", pool=pool_str, content=content)


def _unwrap_nested_list_item(item: str) -> list[str] | None:
    """Recover a list the model accidentally serialized as a single string item.

    Some responses come back as `["['tag-one', 'tag-two']"]` instead of
    `["tag-one", "tag-two"]` — a Python-style list literal (single quotes)
    wrapped inside one JSON string. The outer json.loads succeeds, but yields
    a single "tag" that is actually the whole list as text.
    """
    stripped = item.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return None
    try:
        parsed = ast.literal_eval(stripped)
    except (ValueError, SyntaxError):
        return None
    if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
        return parsed
    return None


def parse_response(text: str) -> list[str]:
    text = text.strip()

    # Strip code fences
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    # Extract from the first [ to the last ]
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidate = text[start:end + 1]
        try:
            result = json.loads(candidate)
            if isinstance(result, list):
                items = [str(item) for item in result]
                flattened = []
                for item in items:
                    nested = _unwrap_nested_list_item(item)
                    flattened.extend(nested if nested is not None else [item])
                # Defensive: drop anything that still looks like a serialized
                # list rather than a single tag/keyword — writing it verbatim
                # would corrupt the post's YAML frontmatter.
                return [i for i in flattened if "[" not in i and "]" not in i]
        except json.JSONDecodeError:
            pass

    # Fallback: regex for strings in quotes (double or single)
    matches = re.findall(r'"([^"]+)"', text) or re.findall(r"'([^']+)'", text)
    if matches:
        return matches

    raise ValueError(f"Could not extract tags from the LLM response: {text[:200]}")


REASONING_BLOCK_RE = re.compile(
    r"<\|channel\>thought.*?<channel\|>|<think>.*?</think>",
    re.DOTALL,
)
REASONING_MARKER_RE = re.compile(r"<\|channel\>\w*|<channel\|>|</?think>")


def strip_reasoning(text: str) -> str:
    """Remove the chain of thought some models (e.g. Gemma) emit inline.

    Drops complete `<|channel>thought ... <channel|>` and `<think> ... </think>`
    blocks, then any stray channel markers left behind. An unclosed opening
    marker means the answer never came, so everything after it is dropped too.
    """
    text = REASONING_BLOCK_RE.sub("", text)
    unclosed = re.search(r"<\|channel\>thought|<think>", text)
    if unclosed:
        text = text[:unclosed.start()]
    return REASONING_MARKER_RE.sub("", text).strip()


def _is_repetition_loop(text: str, threshold: float = 0.5) -> bool:
    """Return True if the response looks like a repetition hallucination.

    Splits on whitespace and commas, then checks whether the most common
    token makes up more than `threshold` of all tokens.
    """
    tokens = [t.strip(".,;:!?\"'").lower() for t in re.split(r"[\s,]+", text) if t.strip()]
    if len(tokens) < 10:
        return False
    top_count = Counter(tokens).most_common(1)[0][1]
    return top_count / len(tokens) > threshold


async def call_llm(engine: Engine, prompt: str, system: str | None = None) -> str:
    """The engine's answer to `prompt`, whatever transport the engine uses.

    Every LLM call in Hugin goes through here; failures raise one of
    drafting.LLM_ERRORS for either transport.
    """
    if engine.transport == TRANSPORT_AGY:
        text = await agy.call(engine.model, prompt, system, engine.timeout)
    else:
        text = await _call_api(engine, prompt, system)
    text = strip_reasoning(text)
    if _is_repetition_loop(text):
        raise ValueError("Model returned a repetition loop — skipping")
    return text


async def _call_api(engine: Engine, prompt: str, system: str | None) -> str:
    headers = {"Content-Type": "application/json", "User-Agent": "hugin/0.1"}
    if engine.api_key:
        headers["Authorization"] = f"Bearer {engine.api_key}"

    messages = []
    if system:
        messages.append({
            "role": "system",
            "content": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        })
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": engine.model,
        "messages": messages,
        "temperature": 0.3,
    }

    async with httpx.AsyncClient(timeout=engine.timeout) as client:
        response = await client.post(
            f"{engine.url}/chat/completions",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()

    try:
        text = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise ValueError(f"Unexpected response format from {engine.id}: {response.text[:200]}") from e
    if not isinstance(text, str):
        raise ValueError(f"{engine.id} returned no text content")
    return text


async def suggest_tags(
    engine: Engine,
    metadata: dict,
    content: str,
    pool_str: str,
) -> list[str]:
    prompt = build_prompt(metadata, content, pool_str)
    response_text = await call_llm(engine, prompt, system=prompts.text("tags.system"))
    return parse_response(response_text)


MAX_SUMMARY_CHARS = 160
MAX_SUMMARY_WORDS = 25
MAX_SHORTEN_RETRIES = 1

def build_summary_prompt(
    metadata: dict,
    content: str,
    words: int = MAX_SUMMARY_WORDS,
    style: str = "Be direct and specific.",
) -> str:
    if _estimate_tokens(content) > MAX_TOKENS_CONTENT:
        content = _truncate_post(metadata, content)

    language = detect_language(content)

    current = metadata.get("description", "")
    if current:
        current_desc = prompts.render("summary.current", description=current).strip() + "\n\n"
    else:
        current_desc = ""

    low = max(words - 5, 5)
    words_range = f"{low}-{words}"

    return prompts.render(
        "summary.prompt",
        language=language, current_desc=current_desc, content=content,
        words_range=words_range, style=style,
    )


def parse_summary_response(text: str) -> str:
    text = text.strip()
    # Strip code fences
    text = re.sub(r"^```\w*\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    # Strip surrounding quotes
    text = text.strip().strip('"').strip("'")
    return text


async def suggest_summary(
    engine: Engine,
    metadata: dict,
    content: str,
    words: int = MAX_SUMMARY_WORDS,
    style: str = "Be direct and specific.",
) -> str:
    prompt = build_summary_prompt(metadata, content, words=words, style=style)
    response_text = await call_llm(engine, prompt)
    summary = parse_summary_response(response_text)

    language = detect_language(content)

    word_count = len(summary.split())
    for _ in range(MAX_SHORTEN_RETRIES):
        if word_count <= words and len(summary) <= MAX_SUMMARY_CHARS:
            break
        shorten_prompt = prompts.render(
            "summary.shorten",
            max_words=words,
            language=language, summary=summary,
        )
        response_text = await call_llm(engine, shorten_prompt)
        summary = parse_summary_response(response_text)
        word_count = len(summary.split())

    # Hard truncate at last word boundary as last resort
    if len(summary) > MAX_SUMMARY_CHARS:
        truncated = summary[:MAX_SUMMARY_CHARS]
        last_space = truncated.rfind(" ")
        if last_space > 0:
            summary = truncated[:last_space]
        else:
            summary = truncated

    return summary


SUGGEST_N_SIMILAR = 5  # titles only, so a generous number is cheap


def build_suggest_prompt(title: str, content: str, similar_titles: list[str]) -> str:
    """The topics prompt, telling the LLM which related posts already exist."""
    existing = ""
    if similar_titles:
        existing = prompts.render(
            "links.suggest_existing", titles="\n".join(f"- {t}" for t in similar_titles)
        ).strip() + "\n"
    return prompts.render("links.suggest", title=title, content=content, existing=existing)


def parse_rerank_response(text: str) -> list[str]:
    """Parse LLM reranking response into list of URLs."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            result = json.loads(text[start:end + 1])
            if isinstance(result, list):
                return [str(item) for item in result]
        except json.JSONDecodeError:
            pass

    return []


def parse_anchor_response(text: str) -> list[dict]:
    """Parse LLM response into list of anchor suggestions."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            result = json.loads(text[start:end + 1])
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    return []


def parse_suggestions(text: str) -> list[str]:
    """Parse LLM response into list of topic suggestions."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            result = json.loads(text[start:end + 1])
            if isinstance(result, list):
                return [str(item) for item in result]
        except json.JSONDecodeError:
            pass

    return []


def parse_news_ideas(text: str) -> list[dict]:
    """Parse LLM response into list of {title, description} dicts."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            result = json.loads(text[start:end + 1])
            if isinstance(result, list):
                return [
                    item for item in result
                    if isinstance(item, dict)
                    and item.get("title")
                    and item.get("description")
                ]
        except json.JSONDecodeError:
            pass
    return []
