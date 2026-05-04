"""News fetching for the post-idea generation flow."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote_plus

import httpx

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Hugin/1.0)"}


@dataclass
class NewsItem:
    title: str
    snippet: str
    source: str = ""


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_rss(xml_text: str, max_items: int, source: str = "") -> list[NewsItem]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items: list[NewsItem] = []
    for item in root.findall(".//item")[:max_items]:
        title = item.findtext("title") or ""
        snippet = _strip_html(item.findtext("description") or "")[:300]
        if title:
            items.append(NewsItem(title=title, snippet=snippet, source=source))
    return items


def fetch_news(query: str, max_items: int = 25) -> list[NewsItem]:
    """Fetch headlines from Google News RSS (global, no geo-restriction)."""
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}"
    r = httpx.get(url, timeout=15, follow_redirects=True, headers=_HEADERS)
    r.raise_for_status()
    return _parse_rss(r.text, max_items, source="Google News")
