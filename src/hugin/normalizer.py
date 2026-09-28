"""Normalização de tags recebidas do LLM."""

import re
import unicodedata
from difflib import SequenceMatcher


def detect_language(content: str) -> str:
    """Simple language detection based on common function words."""
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


def strip_accents(text: str) -> str:
    """Remove accents/diacritics from text."""
    normalized = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")


def sort_key(text: str) -> tuple[str, str]:
    """Sort key that treats accented characters as their base form."""
    return (strip_accents(text), text.lower())


def tag_similarity(a: str, b: str) -> float:
    """Compute similarity between two tags, ignoring accents."""
    a_stripped = strip_accents(a)
    b_stripped = strip_accents(b)
    return SequenceMatcher(None, a_stripped, b_stripped).ratio()


def find_similar_tags(
    target: str,
    pool: dict[str, int],
    limit: int = 10,
    threshold: float = 0.4,
) -> list[tuple[str, int, float]]:
    """Find tags similar to target, sorted by similarity descending.

    Returns list of (tag, count, similarity_score).
    """
    results = []
    for tag, count in pool.items():
        if tag == target:
            continue
        score = tag_similarity(target, tag)
        if score >= threshold:
            results.append((tag, count, score))

    results.sort(key=lambda x: x[2], reverse=True)
    return results[:limit]

ARTICLES = {
    "a", "an", "the",             # EN
    "o", "a", "os", "as",         # PT
    "um", "uma", "uns", "umas",   # PT
    "el", "la", "los", "las",     # ES
    "le", "la", "les",            # FR
    "un", "une", "des",           # FR
}


def normalize_tag(tag: str, strip_articles: bool = True) -> str:
    tag = tag.strip().lower()
    tag = re.sub(r"\s+", "-", tag)

    if strip_articles:
        # Remover artigos apenas no início da tag
        parts = tag.split("-")
        while parts and parts[0] in ARTICLES:
            parts.pop(0)
        tag = "-".join(parts)

    # Truncar a 3 palavras
    parts = tag.split("-")
    if len(parts) > 3:
        parts = parts[:3]
    tag = "-".join(parts)

    # Remover hífens duplicados ou nas pontas
    tag = re.sub(r"-+", "-", tag).strip("-")

    return tag


def normalize_tags(
    raw_tags: list[str],
    existing_tags: list[str],
    pool: dict[str, int],
) -> list[str]:
    pool_lower = {t.lower(): t for t in pool}
    existing_lower = {t.lower() for t in existing_tags}

    result = []
    seen = set()

    for raw in raw_tags:
        tag = normalize_tag(raw)
        if not tag:
            continue

        # Exact match against pool (prefer existing form)
        if tag.lower() in pool_lower:
            tag = pool_lower[tag.lower()]
        else:
            # Fuzzy: if tag is a longer variant of an existing tag, use the existing one
            # e.g. "comunicação-felina" → "comunicação", "comportamento-felino" → "comportamento"
            tag_parts = tag.lower().split("-")
            for pool_tag_lower, pool_tag in pool_lower.items():
                pool_parts = pool_tag_lower.split("-")
                if tag_parts[:len(pool_parts)] == pool_parts and len(tag_parts) > len(pool_parts):
                    tag = pool_tag
                    break

        # Dedup contra tags existentes do post
        if tag.lower() in existing_lower:
            continue

        # Dedup dentro do lote
        if tag.lower() in seen:
            continue

        seen.add(tag.lower())
        result.append(tag)

    return result


def normalize_keyword(keyword: str) -> str:
    """Normalize a keyword: lowercase, accent-free, hyphen-separated.

    Unlike tags, keywords never keep accents/diacritics — they're an
    internal vocabulary for the related-posts algorithm, not reader-facing.
    """
    keyword = strip_accents(keyword.strip())
    keyword = re.sub(r"\s+", "-", keyword)
    keyword = re.sub(r"-+", "-", keyword).strip("-")
    return keyword


def normalize_keywords(
    raw_keywords: list[str],
    existing_keywords: list[str],
    pool: dict[str, int],
) -> list[str]:
    """Normalize LLM-suggested keywords, preferring existing pool spellings."""
    pool_lower = {k.lower(): k for k in pool}
    existing_lower = {k.lower() for k in existing_keywords}

    result = []
    seen = set()

    for raw in raw_keywords:
        keyword = normalize_keyword(raw)
        if not keyword:
            continue

        if keyword.lower() in pool_lower:
            keyword = pool_lower[keyword.lower()]

        if keyword.lower() in existing_lower:
            continue
        if keyword.lower() in seen:
            continue

        seen.add(keyword.lower())
        result.append(keyword)

    return result
