"""Normalização de tags recebidas do LLM."""

import re
import unicodedata
from difflib import SequenceMatcher


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


def normalize_tag(tag: str) -> str:
    tag = tag.strip().lower()
    tag = re.sub(r"\s+", "-", tag)

    # Remover artigos que ficaram como palavras isoladas
    parts = tag.split("-")
    parts = [p for p in parts if p not in ARTICLES]
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
