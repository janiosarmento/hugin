"""Comunicação com endpoint LLM e parse de respostas."""

import json
import re

import httpx

from hugin.engines import Engine

PROMPT_TEMPLATE = """\
You are a blog post tag generator. Analyze the following blog post and suggest relevant tags.

CRITICAL: You MUST pick tags from the EXISTING POOL below whenever possible. Creating a new tag is a LAST RESORT — only when absolutely no existing tag covers the topic. If in doubt, use the existing tag.

RULES:
- Tags must be lowercase
- Multi-word tags use hyphens as separator (e.g., "self-hosted")
- Maximum 3 words per tag
- No articles (a, the, o, um, os, as, les, etc.)
- Keep tags as short as possible
- Suggest between 3 and 7 tags per post
- Tags must be in the same language as the post content
- PRESERVE accents and diacritics (e.g., "automação" not "automacao", "réseau" not "reseau")
- EVERY tag you suggest should ideally already exist in the pool below
- A new tag is acceptable ONLY if the post covers a topic with zero coverage in the existing pool

EXISTING TAGS IN THIS BLOG (use these, most popular first):
{pool}

POST CONTENT:
{content}

Respond with a JSON array of strings, nothing else. Example: ["tag-one", "tag-two", "tag-three"]"""

TOKEN_ESTIMATE_DIVISOR = 4
MAX_TOKENS_CONTENT = 4000


def _estimate_tokens(text: str) -> int:
    return len(text) // TOKEN_ESTIMATE_DIVISOR


def _truncate_post(metadata: dict, content: str) -> str:
    title = metadata.get("title", "")
    description = metadata.get("description", "")

    # Extrair headings
    headings = [line for line in content.splitlines() if line.startswith("#")]

    # Primeiros parágrafos (até ~1000 chars)
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

    return PROMPT_TEMPLATE.format(pool=pool_str, content=content)


def parse_response(text: str) -> list[str]:
    text = text.strip()

    # Strip code fences
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    # Extrair do primeiro [ ao último ]
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidate = text[start:end + 1]
        try:
            result = json.loads(candidate)
            if isinstance(result, list):
                return [str(item) for item in result]
        except json.JSONDecodeError:
            pass

    # Fallback: regex para strings entre aspas
    matches = re.findall(r'"([^"]+)"', text)
    if matches:
        return matches

    raise ValueError(f"Não foi possível extrair tags da resposta do LLM: {text[:200]}")


async def call_llm(engine: Engine, prompt: str) -> str:
    headers = {"Content-Type": "application/json"}
    if engine.api_key:
        headers["Authorization"] = f"Bearer {engine.api_key}"

    payload = {
        "model": engine.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }

    async with httpx.AsyncClient(timeout=engine.timeout) as client:
        response = await client.post(
            f"{engine.url}/chat/completions",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()

    data = response.json()
    return data["choices"][0]["message"]["content"]


async def suggest_tags(
    engine: Engine,
    metadata: dict,
    content: str,
    pool_str: str,
) -> list[str]:
    prompt = build_prompt(metadata, content, pool_str)
    response_text = await call_llm(engine, prompt)
    return parse_response(response_text)


SUMMARY_PROMPT_TEMPLATE = """\
You are a sharp, opinionated tech blogger writing a meta description for one of your own posts.

CRITICAL: Write ONLY in {language}. No other language.

STYLE:
- Write as if you're telling a friend what the post is about — direct, natural, with personality
- Vary your sentence structures — don't start every summary the same way
- NEVER use "Descubra como", "Aprenda como", "Saiba como", "Discover how", "Learn how" or similar formulaic openings
- NEVER use clickbait or generic marketing language
- Be specific about what makes this post interesting or unique
- A good summary states what the post covers and hints at why it matters

FORMAT:
- Strictly between 140 and 160 characters
- Plain text only, no quotes, no explanation

{current_desc}POST CONTENT:
{content}"""


def _detect_language(content: str) -> str:
    """Simple language detection based on common words."""
    sample = content[:2000].lower()
    indicators = {
        "Portuguese": ["não", "como", "para", "este", "uma", "com", "mais", "são", "também", "pode"],
        "English": ["the", "and", "that", "this", "with", "from", "have", "will", "your", "can"],
        "Spanish": ["pero", "puede", "todos", "tiene", "muy", "hacer", "cuando", "donde", "ahora", "hay"],
        "French": ["les", "des", "une", "pour", "dans", "avec", "cette", "sont", "mais", "tout"],
    }
    scores = {}
    for lang, words in indicators.items():
        scores[lang] = sum(1 for w in words if f" {w} " in f" {sample} ")
    return max(scores, key=scores.get) if max(scores.values()) > 0 else "English"


def build_summary_prompt(metadata: dict, content: str) -> str:
    if _estimate_tokens(content) > MAX_TOKENS_CONTENT:
        content = _truncate_post(metadata, content)

    language = _detect_language(content)

    current = metadata.get("description", "")
    if current:
        current_desc = f"CURRENT DESCRIPTION (improve or replace):\n{current}\n\n"
    else:
        current_desc = ""

    return SUMMARY_PROMPT_TEMPLATE.format(
        language=language, current_desc=current_desc, content=content,
    )


def parse_summary_response(text: str) -> str:
    text = text.strip()
    # Strip code fences
    text = re.sub(r"^```\w*\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    # Strip surrounding quotes
    text = text.strip().strip('"').strip("'")
    return text


SHORTEN_PROMPT = """\
The following summary is {length} characters long. Rewrite it to be STRICTLY between 140 and 160 characters. \
Keep the same language ({language}). Keep the same meaning. Respond with the shortened text only, nothing else.

{summary}"""

MAX_SUMMARY_CHARS = 160
MAX_SHORTEN_RETRIES = 2


async def suggest_summary(
    engine: Engine,
    metadata: dict,
    content: str,
) -> str:
    prompt = build_summary_prompt(metadata, content)
    response_text = await call_llm(engine, prompt)
    summary = parse_summary_response(response_text)

    language = _detect_language(content)

    for _ in range(MAX_SHORTEN_RETRIES):
        if len(summary) <= MAX_SUMMARY_CHARS:
            break
        shorten_prompt = SHORTEN_PROMPT.format(
            length=len(summary), language=language, summary=summary,
        )
        response_text = await call_llm(engine, shorten_prompt)
        summary = parse_summary_response(response_text)

    return summary
