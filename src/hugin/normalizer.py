"""Normalização de tags recebidas do LLM."""

import re
import unicodedata


def sort_key(text: str) -> tuple[str, str]:
    """Sort key that treats accented characters as their base form."""
    normalized = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    return (stripped, text.lower())

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
