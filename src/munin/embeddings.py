"""Embedding subsystem — model loading, caching, and similarity search."""

import hashlib
import json
import os
from pathlib import Path

import numpy as np

from hugin.engines import CONFIG_DIR

EMBEDDINGS_DIR = CONFIG_DIR / "embeddings"
CACHE_VERSION = 1
DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"


def _cache_path(posts_dir: Path) -> Path:
    dir_hash = hashlib.md5(str(posts_dir.resolve()).encode()).hexdigest()
    return EMBEDDINGS_DIR / f"{dir_hash}.json"


def _build_text(metadata: dict, summary_field: str) -> str:
    """Build the text to embed for a post."""
    parts = []
    title = metadata.get("title", "")
    if title:
        parts.append(str(title))

    tags = metadata.get("tags", [])
    if tags:
        parts.append(" ".join(str(t) for t in tags))

    summary = metadata.get(summary_field, "")
    if summary:
        parts.append(str(summary))

    return " ".join(parts)


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

    def _save_cache(self) -> None:
        """Write cache to disk."""
        EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(self._cache_path, "w") as f:
            json.dump(self._cache, f)

    def clear_cache(self) -> None:
        """Delete the embedding cache file and reset in-memory cache."""
        if self._cache_path.exists():
            self._cache_path.unlink()
        self._cache = {"version": CACHE_VERSION, "posts": {}}

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

        # Determine which posts need (re)embedding
        stale = []
        current_paths = set()

        for post in posts:
            abs_path = str(post.path.resolve())
            current_paths.add(abs_path)
            mtime = os.path.getmtime(post.path)

            entry = cached.get(abs_path)
            if entry and entry.get("mtime") == mtime:
                continue  # Up to date

            stale.append((post, abs_path, mtime))

        # Prune deleted posts
        for old_path in list(cached.keys()):
            if old_path not in current_paths:
                del cached[old_path]

        if not stale:
            print_fn(f"Embeddings up to date ({len(cached)} posts cached).")
            return

        # Batch encode
        print_fn(f"Building embeddings for {len(stale)} posts...")
        texts = []
        for i, (post, abs_path, mtime) in enumerate(stale):
            text = _build_text(post.metadata, self.summary_field)
            texts.append(text)
            if (i + 1) % 50 == 0 or i == len(stale) - 1:
                print_fn(f"  [{i + 1}/{len(stale)}] {post.filename}")

        embeddings = self._model.encode(texts, show_progress_bar=False)

        # Update cache
        for (post, abs_path, mtime), embedding in zip(stale, embeddings):
            url = url_fn(post.metadata, post.filename)
            cached[abs_path] = {
                "mtime": mtime,
                "url": url,
                "title": post.metadata.get("title", post.filename),
                "embedding": embedding.tolist(),
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
        text = _build_text(post.metadata, self.summary_field)

        # Compute embedding using numpy directly to avoid tqdm/multiprocessing
        # crash inside Textual on Python 3.14
        embedding = self._encode_single(text)

        self._cache["posts"][abs_path] = {
            "mtime": mtime,
            "url": url_fn(post.metadata, post.filename),
            "title": post.metadata.get("title", post.filename),
            "embedding": embedding.tolist(),
        }
        self._save_cache()

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
        if not query_entry:
            return []

        query_vec = np.array(query_entry["embedding"])
        exclude_urls = exclude_urls or set()

        results = []
        for other_path, entry in cached.items():
            if other_path == abs_path:
                continue
            if entry["url"] in exclude_urls:
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

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:n]

    def get_post_url(self, post) -> str | None:
        """Get the cached URL for a post."""
        abs_path = str(post.path.resolve())
        entry = self._cache["posts"].get(abs_path)
        return entry["url"] if entry else None
