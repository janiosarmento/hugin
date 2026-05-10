"""Embedding subsystem — model loading, caching, and similarity search."""

import hashlib
import json
import math
import os
import re
import unicodedata
from pathlib import Path

import numpy as np

from hugin.engines import CONFIG_DIR

EMBEDDINGS_DIR = CONFIG_DIR / "embeddings"
CACHE_VERSION = 6
DEFAULT_MODEL = "intfloat/multilingual-e5-large"


def _cache_path(posts_dir: Path) -> Path:
    dir_hash = hashlib.md5(str(posts_dir.resolve()).encode()).hexdigest()
    return EMBEDDINGS_DIR / f"{dir_hash}.json"


def _keywords_path(posts_dir: Path) -> Path:
    dir_hash = hashlib.md5(str(posts_dir.resolve()).encode()).hexdigest()
    return EMBEDDINGS_DIR / f"{dir_hash}_kw.json"


MAX_EMBED_CHARS = 2000  # ~500 tokens, fits 512-token e5-large


def _slug_keywords(url: str) -> str:
    """Extract keywords from a URL slug, replacing hyphens with spaces."""
    slug = url.strip("/").rsplit("/", 1)[-1] if "/" in url else url
    return slug.replace("-", " ")


_MIN_KEYWORD_LEN = 6
_MENTION_BOOST = 1.0
_TAG_BOOST_MAX = 0.5  # Maximum boost from tag overlap


def _normalize_ascii(text: str) -> str:
    """Lowercase and strip accents for mention matching."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(c)
    )


def _match_keywords(url: str) -> list[str]:
    """Extract significant normalized keywords from URL slug for mention matching."""
    slug = url.strip("/").rsplit("/", 1)[-1] if "/" in url else url
    return [
        _normalize_ascii(k)
        for k in slug.split("-")
        if len(k) >= _MIN_KEYWORD_LEN
    ]


def _mention_score(body_norm: str, url: str) -> float:
    """Fraction of slug keywords that appear as whole words in the body (0.0–1.0)."""
    keywords = _match_keywords(url)
    if not keywords:
        return 0.0
    hits = sum(
        1 for kw in keywords
        if re.search(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", body_norm)
    )
    return hits / len(keywords)


def _build_text(
    metadata: dict,
    summary_field: str,
    content: str = "",
    url: str = "",
    link_keywords: str = "",
) -> str:
    """Build the text to embed for a post.

    Priority: link_keywords > title > description > url keywords > tags > content.
    Truncated to MAX_EMBED_CHARS to stay within model token limits.
    Prefixed with 'query: ' as required by e5 models.
    """
    parts = []

    # LLM-generated link-discovery keywords (highest signal, repeated for weight)
    if link_keywords:
        parts.extend([link_keywords, link_keywords])

    title = metadata.get("title", "")
    if title:
        title_str = str(title)
        parts.extend([title_str, title_str, title_str])

    # Description/summary field
    summary = metadata.get(summary_field, "")
    if summary:
        parts.append(str(summary))

    if url:
        keywords = _slug_keywords(url)
        parts.extend([keywords, keywords])

    tags = metadata.get("tags", [])
    if tags:
        parts.append(" ".join(str(t) for t in tags))

    if content:
        # Extract headings
        headings = [
            line.lstrip("#").strip()
            for line in content.splitlines()
            if line.startswith("#")
        ]
        if headings:
            parts.append(" ".join(headings))

        # Collect content paragraphs (non-empty, non-heading lines)
        for line in content.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                parts.append(stripped)
                if len(" ".join(parts)) >= MAX_EMBED_CHARS:
                    break

    text = " ".join(parts)
    if len(text) > MAX_EMBED_CHARS:
        # Truncate at last word boundary
        truncated = text[:MAX_EMBED_CHARS]
        last_space = truncated.rfind(" ")
        if last_space > 0:
            text = truncated[:last_space]
        else:
            text = truncated
    return "query: " + text


class EmbeddingIndex:
    """Manages embeddings for a blog directory."""

    def __init__(
        self,
        posts_dir: Path,
        model_name: str = DEFAULT_MODEL,
        summary_field: str = "description",
    ) -> None:
        self.posts_dir = posts_dir.resolve()
        self.model_name = model_name
        self.summary_field = summary_field
        self._model = None
        self._cache: dict = {"version": CACHE_VERSION, "posts": {}}
        self._cache_path = _cache_path(self.posts_dir)
        self._keywords_path = _keywords_path(self.posts_dir)
        self._keywords: dict[str, str] = {}  # abs_path → keywords

    def _load_model(self, print_fn=print):
        """Load the sentence-transformers model with ONNX backend."""
        if self._model is not None:
            return

        print_fn(f"Loading embedding model ({self.model_name})...")

        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(self.model_name, backend="onnx")

    def _load_cache(self) -> None:
        """Load existing cache from disk."""
        if self._cache_path.exists():
            try:
                with open(self._cache_path) as f:
                    data = json.load(f)
                if data.get("version") == CACHE_VERSION:
                    self._cache = data
            except (json.JSONDecodeError, KeyError):
                pass
        if self._keywords_path.exists():
            try:
                with open(self._keywords_path) as f:
                    self._keywords = json.load(f)
            except (json.JSONDecodeError, KeyError):
                self._keywords = {}

    def _save_cache(self) -> None:
        """Write cache to disk."""
        EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(self._cache_path, "w") as f:
            json.dump(self._cache, f)

    def _save_keywords(self) -> None:
        """Write link keywords to their own file (survives cache clears)."""
        EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(self._keywords_path, "w") as f:
            json.dump(self._keywords, f)

    def clear_cache(self) -> None:
        """Delete the embedding cache file and reset in-memory cache.

        Keywords are intentionally preserved in their own file.
        """
        if self._cache_path.exists():
            self._cache_path.unlink()
        self._cache = {"version": CACHE_VERSION, "posts": {}}

    def clear_keywords(self) -> None:
        """Delete the keyword cache file and reset in-memory keywords."""
        if self._keywords_path.exists():
            self._keywords_path.unlink()
        self._keywords = {}

    def remove_post(self, post) -> None:
        """Remove a single post from both caches (embedding + keywords)."""
        abs_path = str(post.path.resolve())
        if abs_path in self._cache.get("posts", {}):
            del self._cache["posts"][abs_path]
            self._save_cache()
        if abs_path in self._keywords:
            del self._keywords[abs_path]
            self._save_keywords()

    def build(
        self,
        posts: list,
        url_fn,
        print_fn=print,
    ) -> None:
        """Build/update the embedding index for all posts.

        Args:
            posts: list of hugin.scanner.Post objects
            url_fn: callable(metadata, filename) -> str (URL resolver)
            print_fn: callable for progress output
        """
        self._load_model(print_fn)
        self._load_cache()

        cached = self._cache["posts"]

        # Filter out drafts — they should not be in the embedding index
        published = [p for p in posts if not p.metadata.get("draft")]

        # Determine which posts need (re)embedding
        stale = []
        current_paths = set()

        for post in published:
            abs_path = str(post.path.resolve())
            current_paths.add(abs_path)
            mtime = os.path.getmtime(post.path)

            entry = cached.get(abs_path)
            if entry and entry.get("mtime") == mtime:
                continue  # Up to date

            stale.append((post, abs_path, mtime))

        # Prune deleted posts and drafts
        for old_path in list(cached.keys()):
            if old_path not in current_paths:
                del cached[old_path]

        # Re-resolve URLs for all cached entries (catches Hugo config changes)
        urls_updated = 0
        posts_by_path = {str(p.path.resolve()): p for p in published}
        for path_key, entry in cached.items():
            post_obj = posts_by_path.get(path_key)
            if post_obj:
                fresh_url = url_fn(post_obj.metadata, post_obj.filename)
                if entry["url"] != fresh_url:
                    entry["url"] = fresh_url
                    urls_updated += 1
                # Also refresh title and tags in case they changed
                fresh_title = post_obj.metadata.get("title", post_obj.filename)
                if entry.get("title") != fresh_title:
                    entry["title"] = fresh_title
                entry["tags"] = post_obj.metadata.get("tags", [])

        if not stale:
            if urls_updated:
                self._save_cache()
                print_fn(f"URLs updated for {urls_updated} posts.")
            print_fn(f"Embeddings up to date ({len(cached)} posts cached).")
            return

        # Batch encode
        print_fn(f"Building embeddings for {len(stale)} posts...")
        texts = []
        for i, (post, abs_path, mtime) in enumerate(stale):
            url = url_fn(post.metadata, post.filename)
            existing_keywords = self._keywords.get(abs_path, "")
            text = _build_text(post.metadata, self.summary_field, post.content, url, existing_keywords)
            texts.append(text)
            if (i + 1) % 50 == 0 or i == len(stale) - 1:
                print_fn(f"  [{i + 1}/{len(stale)}] {post.filename}")

        embeddings = self._model.encode(texts, show_progress_bar=False)

        # Update cache
        for (post, abs_path, mtime), embedding in zip(stale, embeddings):
            url = url_fn(post.metadata, post.filename)
            old_entry = cached.get(abs_path, {})
            cached[abs_path] = {
                "mtime": mtime,
                "url": url,
                "title": post.metadata.get("title", post.filename),
                "tags": post.metadata.get("tags", []),
                "embedding": embedding.tolist(),
                "no_outgoing": old_entry.get("no_outgoing", False),
            }

        self._save_cache()
        print_fn(f"Done. {len(cached)} posts indexed.")

    def _encode_single(self, text: str):
        """Encode a single text without tqdm (safe inside Textual)."""
        import torch

        features = self._model.tokenize([text])

        # Redirect native fd 2 (stderr) to /dev/null to suppress
        # ONNX Runtime C-level warnings that bypass Python's sys.stderr
        stderr_fd = os.dup(2)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 2)
        os.close(devnull)
        try:
            with torch.no_grad():
                out = self._model.forward(features)
        finally:
            os.dup2(stderr_fd, 2)
            os.close(stderr_fd)

        embedding = out["sentence_embedding"][0].cpu().numpy()
        return embedding

    def update_post(self, post, url_fn) -> None:
        """Recompute embedding for a single post after modification."""
        if self._model is None:
            return

        abs_path = str(post.path.resolve())
        mtime = os.path.getmtime(post.path)
        url = url_fn(post.metadata, post.filename)
        old_entry = self._cache["posts"].get(abs_path, {})
        link_keywords = self._keywords.get(abs_path, "")
        text = _build_text(post.metadata, self.summary_field, post.content, url, link_keywords)

        embedding = self._encode_single(text)

        self._cache["posts"][abs_path] = {
            "mtime": mtime,
            "url": url,
            "title": post.metadata.get("title", post.filename),
            "tags": post.metadata.get("tags", []),
            "embedding": embedding.tolist(),
            "no_outgoing": old_entry.get("no_outgoing", False),
        }
        self._save_cache()

    def get_link_keywords(self, post) -> str:
        """Return cached link_keywords for a post, or empty string if none."""
        abs_path = str(post.path.resolve())
        return self._keywords.get(abs_path, "")

    @staticmethod
    def _normalize_keywords(keywords: str) -> str | None:
        """Deduplicate and validate a comma-separated keyword string.

        Returns the cleaned string, or None if the response looks like a
        hallucination (repetition loop or rambling paragraph).
        """
        parts = [k.strip() for k in keywords.split(",") if k.strip()]
        if not parts:
            return None
        # Deduplicate preserving order (case-insensitive)
        seen: set[str] = set()
        deduped: list[str] = []
        for p in parts:
            key = p.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(p)
        # Reject if we ended up with fewer than 3 unique keywords
        if len(deduped) < 3:
            return None
        # Reject if any single keyword is suspiciously long (paragraph fragment)
        if any(len(k.split()) > 6 for k in deduped):
            return None
        return ", ".join(deduped)

    def store_link_keywords(self, post, keywords: str) -> None:
        """Persist link keywords without re-encoding the embedding.

        Use this for batch generation; embeddings will pick up the new keywords
        on the next build() call.
        """
        cleaned = self._normalize_keywords(keywords)
        if not cleaned:
            raise ValueError("Keywords failed validation (repetition or malformed)")
        abs_path = str(post.path.resolve())
        self._keywords[abs_path] = cleaned
        self._save_keywords()

    def set_link_keywords(self, post, url_fn, keywords: str) -> None:
        """Store LLM-generated link keywords and re-encode the post embedding."""
        if self._model is None:
            return
        cleaned = self._normalize_keywords(keywords)
        if not cleaned:
            raise ValueError("Keywords failed validation (repetition or malformed)")
        abs_path = str(post.path.resolve())
        self._keywords[abs_path] = cleaned
        self._save_keywords()
        old_entry = self._cache["posts"].get(abs_path, {})
        url = url_fn(post.metadata, post.filename)
        text = _build_text(post.metadata, self.summary_field, post.content, url, keywords)
        embedding = self._encode_single(text)
        self._cache["posts"][abs_path] = {
            **old_entry,
            "embedding": embedding.tolist(),
        }
        self._save_cache()

    def mark_no_outgoing(self, post) -> None:
        """Mark a post as having no outgoing link opportunities."""
        abs_path = str(post.path.resolve())
        entry = self._cache["posts"].get(abs_path)
        if entry:
            entry["no_outgoing"] = True
            self._save_cache()

    def has_no_outgoing(self, post) -> bool:
        """Check if a post was previously marked as having no outgoing opportunities."""
        abs_path = str(post.path.resolve())
        entry = self._cache["posts"].get(abs_path)
        return bool(entry and entry.get("no_outgoing"))

    def find_similar(
        self,
        post,
        n: int = 10,
        exclude_urls: set[str] | None = None,
    ) -> list[dict]:
        """Find the top N most similar posts by cosine similarity.

        Args:
            post: the query post
            n: number of results
            exclude_urls: URLs to exclude (e.g. posts already linked to)

        Returns:
            List of dicts with keys: path, url, title, score
        """
        abs_path = str(post.path.resolve())
        cached = self._cache["posts"]

        query_entry = cached.get(abs_path)
        if query_entry:
            query_vec = np.array(query_entry["embedding"])
        elif self._model is not None:
            # Draft or uncached post — compute embedding on the fly
            slug_url = post.filename.rsplit(".", 1)[0]
            text = _build_text(post.metadata, self.summary_field, post.content, slug_url)
            query_vec = self._encode_single(text)
        else:
            return []
        exclude_urls = exclude_urls or set()

        results = []
        for other_path, entry in cached.items():
            if other_path == abs_path:
                continue
            if entry["url"] in exclude_urls:
                continue
            if not Path(other_path).exists():
                continue

            other_vec = np.array(entry["embedding"])
            # Cosine similarity
            dot = np.dot(query_vec, other_vec)
            norm = np.linalg.norm(query_vec) * np.linalg.norm(other_vec)
            score = float(dot / norm) if norm > 0 else 0.0

            results.append({
                "path": other_path,
                "url": entry["url"],
                "title": entry["title"],
                "score": score,
            })

        # Boost posts explicitly mentioned in the body
        body_norm = _normalize_ascii(post.content) if hasattr(post, "content") and post.content else ""
        if body_norm:
            for r in results:
                r["score"] += _MENTION_BOOST * _mention_score(body_norm, r["url"])

        # Boost posts sharing tags (weighted by rarity)
        query_tags = set(post.metadata.get("tags", []) if hasattr(post, "metadata") else [])
        if query_tags:
            # Build tag document frequency from cache
            total_posts = len(cached)
            tag_df: dict[str, int] = {}
            for entry in cached.values():
                for t in entry.get("tags", []):
                    tag_df[t] = tag_df.get(t, 0) + 1

            # IDF weight per tag: rare tags score higher
            tag_idf = {}
            for t, df in tag_df.items():
                tag_idf[t] = math.log(total_posts / df) if df > 0 else 0.0

            # Max possible IDF score for normalization
            max_idf_sum = sum(tag_idf.get(t, 0) for t in query_tags) or 1.0

            for r in results:
                other_entry = cached.get(r["path"], {})
                other_tags = set(other_entry.get("tags", []))
                shared = query_tags & other_tags
                if shared:
                    idf_sum = sum(tag_idf.get(t, 0) for t in shared)
                    r["score"] += _TAG_BOOST_MAX * (idf_sum / max_idf_sum)

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:n]

    def find_by_shared_tags(
        self,
        post,
        n: int = 10,
        exclude_urls: set[str] | None = None,
        min_shared: int = 2,
    ) -> list[dict]:
        """Find posts sharing at least min_shared tags with the query post.

        Scored by IDF-weighted tag overlap so rare shared tags rank higher.
        """
        abs_path = str(post.path.resolve())
        cached = self._cache["posts"]
        exclude_urls = exclude_urls or set()

        query_tags = set(post.metadata.get("tags", []) if hasattr(post, "metadata") else [])
        if not query_tags:
            return []

        # Build tag IDF (same logic as find_similar)
        total_posts = len(cached)
        tag_df: dict[str, int] = {}
        for entry in cached.values():
            for t in entry.get("tags", []):
                tag_df[t] = tag_df.get(t, 0) + 1
        tag_idf = {t: math.log(total_posts / df) if df > 0 else 0.0 for t, df in tag_df.items()}
        max_idf_sum = sum(tag_idf.get(t, 0) for t in query_tags) or 1.0

        results = []
        for other_path, entry in cached.items():
            if other_path == abs_path:
                continue
            if entry["url"] in exclude_urls:
                continue
            if not Path(other_path).exists():
                continue

            other_tags = set(entry.get("tags", []))
            shared = query_tags & other_tags
            if len(shared) < min_shared:
                continue

            idf_sum = sum(tag_idf.get(t, 0) for t in shared)
            score = idf_sum / max_idf_sum

            results.append({
                "path": other_path,
                "url": entry["url"],
                "title": entry["title"],
                "score": score,
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:n]

    def get_post_url(self, post) -> str | None:
        """Get the cached URL for a post."""
        abs_path = str(post.path.resolve())
        entry = self._cache["posts"].get(abs_path)
        return entry.get("url") if entry else None
