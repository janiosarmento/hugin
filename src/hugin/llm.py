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


def _is_repetition_loop(text: str, threshold: float = 0.5) -> bool:
    """Return True if the response looks like a repetition hallucination.

    Splits on whitespace and commas, then checks whether the most common
    token makes up more than `threshold` of all tokens.
    """
    import re
    tokens = [t.strip(".,;:!?\"'").lower() for t in re.split(r"[\s,]+", text) if t.strip()]
    if len(tokens) < 10:
        return False
    from collections import Counter
    top_count = Counter(tokens).most_common(1)[0][1]
    return top_count / len(tokens) > threshold


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
    text = data["choices"][0]["message"]["content"]
    if _is_repetition_loop(text):
        raise ValueError("Model returned a repetition loop — skipping")
    return text


async def suggest_tags(
    engine: Engine,
    metadata: dict,
    content: str,
    pool_str: str,
) -> list[str]:
    prompt = build_prompt(metadata, content, pool_str)
    response_text = await call_llm(engine, prompt)
    return parse_response(response_text)


MAX_SUMMARY_CHARS = 160
MAX_SUMMARY_WORDS = 25
MAX_SHORTEN_RETRIES = 1

SUMMARY_PROMPT_TEMPLATE = """\
Write a meta description for this blog post. {language} only. {words_range} words, one sentence, no quotes.

NO "Descubra", "Aprenda", "Saiba", "Discover", "Learn". {style}

{current_desc}POST:
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


def build_summary_prompt(
    metadata: dict,
    content: str,
    words: int = MAX_SUMMARY_WORDS,
    style: str = "Be direct and specific.",
) -> str:
    if _estimate_tokens(content) > MAX_TOKENS_CONTENT:
        content = _truncate_post(metadata, content)

    language = _detect_language(content)

    current = metadata.get("description", "")
    if current:
        current_desc = f"CURRENT DESCRIPTION (improve or replace):\n{current}\n\n"
    else:
        current_desc = ""

    low = max(words - 5, 5)
    words_range = f"{low}-{words}"

    return SUMMARY_PROMPT_TEMPLATE.format(
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


SHORTEN_PROMPT = """\
Rewrite in at most {max_words} words. {language}. Only the text, nothing else.

{summary}"""



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

    language = _detect_language(content)

    word_count = len(summary.split())
    for _ in range(MAX_SHORTEN_RETRIES):
        if word_count <= words and len(summary) <= MAX_SUMMARY_CHARS:
            break
        shorten_prompt = SHORTEN_PROMPT.format(
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


# --- Link prompts (from Munin) ---

ANCHOR_SYSTEM_PROMPT = """\
You are a technical blog editor. Your task is to identify natural anchor text within a blog post body that could serve as an internal link to related posts.

Rules:
- The anchor_text must appear verbatim in the post body.
- Prefer MULTI-WORD phrases over single words when the full phrase appears in the text. A compound term ("leucemia felina", "doença renal crônica", "sistema imune") is a better anchor than any single word from it.
- Anchors must be between 1 and {max_anchor_words} words. Prefer the longest meaningful phrase that fits naturally, not the shortest. Never use full sentences.
- Do not suggest anchors inside headings, code blocks, inline code, images, or existing links.
- Suggest at most one anchor per candidate post.
- Omit candidates for which no natural anchor exists — do not force one.
- Return a JSON array and nothing else. No preamble, no markdown fences."""

ANCHOR_USER_TEMPLATE = """\
Post body:
{body}

Candidate posts (suggest an anchor for each where natural):
{candidates_json}

Return format:
[{{"target_url": "/posts/foo/", "anchor_text": "exact phrase from body"}}]"""

SUGGEST_PROMPT = """\
You are a blog content strategist. Based on the following blog post, suggest 5 to 10 topics \
for NEW posts that would complement this one. These should be topics that a reader of this \
post would naturally want to read next.

RULES:
- Each suggestion should be a specific, actionable post title
- Titles must be in the same language as the post content
- Be specific — not "more about X" but a concrete angle or question
- Return a JSON array of strings, nothing else

POST TITLE: {title}

POST CONTENT:
{content}

Return format: ["Post title 1", "Post title 2", ...]"""

RERANK_PROMPT = """\
You are a blog editor. Given a blog post and a list of candidate posts, select the \
candidates that are related to the post content. A post is related if a reader of the \
current post would benefit from reading it — topical overlap, shared concepts, \
complementary information, or direct references.

Be INCLUSIVE rather than strict: keep candidates with meaningful topical overlap, \
even if indirect or partial. Only reject posts that are clearly unrelated to the topic. \
When in doubt, keep the candidate — a human editor will make the final call.

POST TITLE: {title}

POST BODY (first 2000 chars):
{body}

CANDIDATES:
{candidates_json}

Return a JSON array with ONLY the URLs of relevant candidates, nothing else.
Example: ["/posts/foo/", "/posts/bar/"]"""

LINK_KEYWORDS_PROMPT = """\
Extract 10 to 15 keywords and key concepts from this blog post to help find related posts \
for internal linking. Focus on: main topic, specific subjects mentioned (breeds, conditions, \
products, techniques), and core concepts discussed. Avoid generic words.

Return only a comma-separated list of keywords, nothing else.

POST TITLE: {title}

POST CONTENT:
{content}"""

RETRY_PROMPT = """\
The phrase '{anchor_text}' does not appear verbatim in the post body.
Choose a phrase from the body that exists exactly as written and would
naturally link to: {title} ({url})

Post body:
{body}"""


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


NEWS_IDEAS_PROMPT = """\
You are a content strategist. Based on the news items below about "{query}", \
generate blog post ideas.

NEWS ITEMS:
{news_items}

Generate {n_ideas} engaging post ideas inspired by these news. \
Each idea should be a topic readers actively search for — not a news summary.

{categories_instruction}

Return a JSON array only, no explanation:
[{{"title": "Post title here", "description": "1-2 sentences on what the post covers"{category_field}}}]"""


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
