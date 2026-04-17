# Hugin + Munin Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge Munin's internal-link management into Hugin, creating a single unified TUI with all functionality accessible from one screen, plus a new built-in Markdown editor.

**Architecture:** Move Munin's pure modules (linker, embeddings, hugo, config) into the `hugin` package. Fuse `MuninScreen` and `ReviewScreen` into a single `HuginScreen`. Add `EditorScreen` as a new push-screen for in-place editing. Delete the `munin` package entirely.

**Tech Stack:** Python 3.11+, Textual (TUI), httpx (LLM), python-frontmatter, sentence-transformers, Click (CLI)

---

## File Structure

### Files to create
- `src/hugin/config.py` — Links config (from munin/config.py, with fallback logic)
- `src/hugin/linker.py` — Markdown link detection and application (from munin/linker.py)
- `src/hugin/embeddings.py` — Embedding index (from munin/embeddings.py)
- `src/hugin/hugo.py` — Hugo config parsing and URL inference (from munin/hugo.py)
- `src/hugin/tui/editor.py` — New EditorScreen

### Files to modify
- `src/hugin/llm.py` — Add Munin's prompts and parse functions
- `src/hugin/tui/app.py` — Accept new dependencies (site, index, config)
- `src/hugin/tui/review.py` — Fuse MuninScreen logic into unified HuginScreen
- `src/hugin/cli.py` — Merge Munin's init flow (embeddings, HugoSite, config, restart loop)
- `pyproject.toml` — Remove `munin` entry point
- `tests/test_llm.py` — Add tests for anchor/suggestion parse functions
- `tests/test_linker.py` — Move from tests/munin/ with updated imports
- `tests/test_hugo.py` — Move from tests/munin/ with updated imports

### Files to delete
- `src/munin/` — Entire package
- `tests/munin/` — Entire directory

---

### Task 1: Move pure modules into hugin package

These are straight file moves with import path updates. No logic changes.

**Files:**
- Create: `src/hugin/linker.py` (from `src/munin/linker.py`)
- Create: `src/hugin/embeddings.py` (from `src/munin/embeddings.py`)
- Create: `src/hugin/hugo.py` (from `src/munin/hugo.py`)
- Move: `tests/munin/test_linker.py` → `tests/test_linker.py`
- Move: `tests/munin/test_hugo.py` → `tests/test_hugo.py`

- [ ] **Step 1: Move linker.py**

```bash
cp src/munin/linker.py src/hugin/linker.py
```

No import changes needed — `linker.py` only imports from `frontmatter` and `markdown_it`, no internal package imports.

- [ ] **Step 2: Move embeddings.py**

```bash
cp src/munin/embeddings.py src/hugin/embeddings.py
```

No import changes needed — it already imports `CONFIG_DIR` from `hugin.engines`.

- [ ] **Step 3: Move hugo.py**

```bash
cp src/munin/hugo.py src/hugin/hugo.py
```

No import changes needed — `hugo.py` only imports from stdlib and `yaml`.

- [ ] **Step 4: Move test files and update imports**

```bash
cp tests/munin/test_linker.py tests/test_linker.py
cp tests/munin/test_hugo.py tests/test_hugo.py
```

In `tests/test_linker.py`, replace the import block:

```python
# OLD
from munin.linker import (
    apply_links,
    convert_html_links_to_markdown,
    extract_existing_links,
    find_protected_zones,
    is_in_protected_zone,
    write_post_with_links,
)

# NEW
from hugin.linker import (
    apply_links,
    convert_html_links_to_markdown,
    extract_existing_links,
    find_protected_zones,
    is_in_protected_zone,
    write_post_with_links,
)
```

In `tests/test_hugo.py`, replace:

```python
# OLD
from munin.hugo import (
    HugoSite,
    find_hugo_config,
    infer_section,
    resolve_url,
    slug_from_filename,
)

# NEW
from hugin.hugo import (
    HugoSite,
    find_hugo_config,
    infer_section,
    resolve_url,
    slug_from_filename,
)
```

- [ ] **Step 5: Run tests to verify moves**

Run: `pytest tests/test_linker.py tests/test_hugo.py -v`
Expected: All tests PASS (same tests, new import paths).

- [ ] **Step 6: Commit**

```bash
git add src/hugin/linker.py src/hugin/embeddings.py src/hugin/hugo.py tests/test_linker.py tests/test_hugo.py
git commit -m "Move linker, embeddings, hugo modules from munin to hugin"
```

---

### Task 2: Move config.py with links.toml fallback

**Files:**
- Create: `src/hugin/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests for config loading with fallback**

Create `tests/test_config.py`:

```python
"""Tests for hugin/config.py — config loading with fallback."""

import pytest
from pathlib import Path
from unittest.mock import patch

from hugin.config import load_config, LinksConfig, EmbeddingsConfig, FrontmatterConfig


class TestLoadConfig:
    def test_loads_from_links_toml(self, tmp_path):
        config_dir = tmp_path / ".hugin"
        config_dir.mkdir()
        (config_dir / "links.toml").write_text(
            "[links]\nmax_per_post = 5\n\n[embeddings]\n\n[frontmatter]\n"
        )

        with patch("hugin.config.CONFIG_DIR", config_dir):
            cfg = load_config()

        assert cfg.links.max_per_post == 5

    def test_fallback_to_munin_toml(self, tmp_path, capsys):
        config_dir = tmp_path / ".hugin"
        config_dir.mkdir()
        (config_dir / "munin.toml").write_text(
            "[links]\nmax_per_post = 12\n\n[embeddings]\n\n[frontmatter]\n"
        )

        with patch("hugin.config.CONFIG_DIR", config_dir):
            cfg = load_config()
            captured = capsys.readouterr()

        assert cfg.links.max_per_post == 12
        assert "munin.toml" in captured.err or "munin.toml" in captured.out

    def test_links_toml_takes_precedence(self, tmp_path):
        config_dir = tmp_path / ".hugin"
        config_dir.mkdir()
        (config_dir / "links.toml").write_text(
            "[links]\nmax_per_post = 3\n\n[embeddings]\n\n[frontmatter]\n"
        )
        (config_dir / "munin.toml").write_text(
            "[links]\nmax_per_post = 99\n\n[embeddings]\n\n[frontmatter]\n"
        )

        with patch("hugin.config.CONFIG_DIR", config_dir):
            cfg = load_config()

        assert cfg.links.max_per_post == 3

    def test_creates_default_when_missing(self, tmp_path):
        config_dir = tmp_path / ".hugin"

        with patch("hugin.config.CONFIG_DIR", config_dir):
            cfg = load_config()

        assert cfg.links.max_per_post == 8
        assert cfg.links.max_anchor_words == 5
        assert cfg.embeddings.model == "paraphrase-multilingual-MiniLM-L12-v2"
        assert (config_dir / "links.toml").exists()

    def test_default_values(self, tmp_path):
        config_dir = tmp_path / ".hugin"
        config_dir.mkdir()
        (config_dir / "links.toml").write_text(
            "[links]\n\n[embeddings]\n\n[frontmatter]\n"
        )

        with patch("hugin.config.CONFIG_DIR", config_dir):
            cfg = load_config()

        assert cfg.links.max_per_post == 8
        assert cfg.links.max_per_paragraph == 1
        assert cfg.links.words_per_link == 300
        assert cfg.links.candidates == 10
        assert cfg.links.max_anchor_words == 5
        assert cfg.frontmatter.summary_field == "description"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hugin.config'`

- [ ] **Step 3: Write the config module**

Create `src/hugin/config.py`:

```python
"""Links configuration management."""

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from hugin.engines import CONFIG_DIR

LINKS_CONFIG_PATH = CONFIG_DIR / "links.toml"
LEGACY_CONFIG_PATH = CONFIG_DIR / "munin.toml"

DEFAULT_CONFIG = """\
[links]
max_per_post      = 8    # hard ceiling on outgoing links per post
max_per_paragraph = 1    # maximum links inserted into any single paragraph
words_per_link    = 300  # 1 link suggested per N words; result capped by max_per_post
candidates        = 10   # how many posts the embedding step returns as candidates
max_anchor_words  = 5    # maximum words in an anchor phrase (longer anchors are discarded)

[embeddings]
model = "paraphrase-multilingual-MiniLM-L12-v2"

[frontmatter]
summary_field = "description"  # field read for embedding
"""


@dataclass
class LinksConfig:
    max_per_post: int = 8
    max_per_paragraph: int = 1
    words_per_link: int = 300
    candidates: int = 10
    max_anchor_words: int = 5


@dataclass
class EmbeddingsConfig:
    model: str = "paraphrase-multilingual-MiniLM-L12-v2"


@dataclass
class FrontmatterConfig:
    summary_field: str = "description"


@dataclass
class HuginConfig:
    links: LinksConfig
    embeddings: EmbeddingsConfig
    frontmatter: FrontmatterConfig


def _resolve_config_path() -> Path:
    """Find config file: links.toml > munin.toml (with warning) > create default."""
    if LINKS_CONFIG_PATH.exists():
        return LINKS_CONFIG_PATH

    if LEGACY_CONFIG_PATH.exists():
        print(
            f"Warning: {LEGACY_CONFIG_PATH} is deprecated, rename to {LINKS_CONFIG_PATH.name}",
            file=sys.stderr,
        )
        return LEGACY_CONFIG_PATH

    # Create default
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LINKS_CONFIG_PATH.write_text(DEFAULT_CONFIG)
    return LINKS_CONFIG_PATH


def load_config() -> HuginConfig:
    """Load links configuration with fallback from munin.toml."""
    path = _resolve_config_path()

    with open(path, "rb") as f:
        data = tomllib.load(f)

    links_data = data.get("links", {})
    embed_data = data.get("embeddings", {})
    fm_data = data.get("frontmatter", {})

    return HuginConfig(
        links=LinksConfig(
            max_per_post=links_data.get("max_per_post", 8),
            max_per_paragraph=links_data.get("max_per_paragraph", 1),
            words_per_link=links_data.get("words_per_link", 300),
            candidates=links_data.get("candidates", 10),
            max_anchor_words=links_data.get("max_anchor_words", 5),
        ),
        embeddings=EmbeddingsConfig(
            model=embed_data.get("model", "paraphrase-multilingual-MiniLM-L12-v2"),
        ),
        frontmatter=FrontmatterConfig(
            summary_field=fm_data.get("summary_field", "description"),
        ),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hugin/config.py tests/test_config.py
git commit -m "Add hugin/config.py with links.toml fallback from munin.toml"
```

---

### Task 3: Move prompts and parse functions to hugin/llm.py

**Files:**
- Modify: `src/hugin/llm.py`
- Modify: `tests/test_llm.py`

- [ ] **Step 1: Write failing tests for the new parse functions**

Append to `tests/test_llm.py`:

```python
from hugin.llm import parse_anchor_response, parse_suggestions


class TestParseAnchorResponse:
    def test_clean_json(self):
        text = '[{"target_url": "/posts/foo/", "anchor_text": "foo bar"}]'
        result = parse_anchor_response(text)
        assert len(result) == 1
        assert result[0]["target_url"] == "/posts/foo/"
        assert result[0]["anchor_text"] == "foo bar"

    def test_with_code_fences(self):
        text = '```json\n[{"target_url": "/posts/foo/", "anchor_text": "foo"}]\n```'
        result = parse_anchor_response(text)
        assert len(result) == 1

    def test_with_preamble(self):
        text = 'Here are the results:\n[{"target_url": "/x/", "anchor_text": "x"}]'
        result = parse_anchor_response(text)
        assert len(result) == 1

    def test_empty_array(self):
        result = parse_anchor_response("[]")
        assert result == []

    def test_invalid_json(self):
        result = parse_anchor_response("not json at all")
        assert result == []


class TestParseSuggestions:
    def test_clean_json(self):
        text = '["Post about X", "Post about Y"]'
        result = parse_suggestions(text)
        assert result == ["Post about X", "Post about Y"]

    def test_with_code_fences(self):
        text = '```json\n["A", "B", "C"]\n```'
        result = parse_suggestions(text)
        assert result == ["A", "B", "C"]

    def test_invalid_json(self):
        result = parse_suggestions("no json here")
        assert result == []

    def test_empty_array(self):
        result = parse_suggestions("[]")
        assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm.py::TestParseAnchorResponse tests/test_llm.py::TestParseSuggestions -v`
Expected: FAIL with `ImportError: cannot import name 'parse_anchor_response'`

- [ ] **Step 3: Add prompts and parse functions to llm.py**

Append to `src/hugin/llm.py` (after the existing `suggest_summary` function):

```python
# --- Link prompts (from Munin) ---

ANCHOR_SYSTEM_PROMPT = """\
You are a technical blog editor. Your task is to identify natural anchor text within a blog post body that could serve as an internal link to related posts.

Rules:
- The anchor_text must appear verbatim in the post body.
- Keep anchors SHORT: 1 to {max_anchor_words} words. Prefer specific technical terms, tool names, or concepts. Never use full sentences or long phrases.
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

RETRY_PROMPT = """\
The phrase '{anchor_text}' does not appear verbatim in the post body.
Choose a phrase from the body that exists exactly as written and would
naturally link to: {title} ({url})

Post body:
{body}"""


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm.py -v`
Expected: All tests PASS (old + new).

- [ ] **Step 5: Commit**

```bash
git add src/hugin/llm.py tests/test_llm.py
git commit -m "Add anchor and suggestion prompts/parsers to hugin/llm.py"
```

---

### Task 4: Unify CLI entry point

**Files:**
- Modify: `src/hugin/cli.py`

The unified CLI adds: HugoSite resolution, embedding index build, config loading, and the restart loop from Munin.

- [ ] **Step 1: Rewrite cli.py**

Replace the entire content of `src/hugin/cli.py`:

```python
"""Entrypoint CLI."""

from datetime import datetime
from pathlib import Path

import click

from hugin.config import load_config
from hugin.embeddings import EmbeddingIndex
from hugin.engines import get_engine
from hugin.hugo import HugoSite
from hugin.scanner import (
    collect_tag_pool,
    find_duplicate_tags,
    load_posts,
)
from hugin.state import load_state


@click.command()
@click.argument(
    "directory",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option("--batch", default=0, help="Maximo de posts a processar (0 = todos).")
@click.option("--report", is_flag=True, help="Exibir estatisticas sem chamar LLM.")
@click.option("--engine", "engine_id", default=None, help="ID do motor de AI.")
@click.option("--model", "model_override", default=None, help="Override do modelo.")
def main(
    directory: Path,
    batch: int,
    report: bool,
    engine_id: str | None,
    model_override: str | None,
) -> None:
    """hugin: manage Hugo blog posts — tags, summaries, links, and editing."""
    directory = directory.resolve()
    config = load_config()

    posts = load_posts(directory)
    if not posts:
        click.echo("Nenhum post .md encontrado no diretorio.")
        raise SystemExit(1)

    # Hugo URL resolution
    site = HugoSite(directory)
    for w in site.warnings:
        click.echo(f"Warning: {w}")

    # Build/update embedding index
    index = EmbeddingIndex(
        posts_dir=directory,
        model_name=config.embeddings.model,
        summary_field=config.frontmatter.summary_field,
    )
    index.build(posts=posts, url_fn=site.post_url, print_fn=click.echo)

    if report:
        _show_report(posts, directory, index)
        return

    state = load_state(directory)

    all_sorted = sorted(posts, key=lambda p: p.date or datetime.min, reverse=True)
    batch_posts = all_sorted if batch == 0 else all_sorted[:batch]

    engine = get_engine(engine_id)
    if model_override:
        engine.model = model_override

    if not engine.available:
        click.echo(
            f"Motor '{engine.id}' sem API key. "
            f"Defina {engine.id.upper()}_API_KEY no ambiente."
        )
        raise SystemExit(1)

    pool = collect_tag_pool(posts)

    from hugin.tui.app import HuginApp

    while True:
        app = HuginApp(
            posts=batch_posts,
            all_posts=posts,
            engine=engine,
            pool=pool,
            state=state,
            directory=directory,
            config=config,
            site=site,
            index=index,
        )
        app.run()

        if app.return_code == 42:
            # Restart: rebuild everything
            click.echo("Restarting...")
            posts = load_posts(directory)
            all_sorted = sorted(posts, key=lambda p: p.date or datetime.min, reverse=True)
            batch_posts = all_sorted if batch == 0 else all_sorted[:batch]
            pool = collect_tag_pool(posts)
            index = EmbeddingIndex(
                posts_dir=directory,
                model_name=config.embeddings.model,
                summary_field=config.frontmatter.summary_field,
            )
            index.build(posts=posts, url_fn=site.post_url, print_fn=click.echo)
            continue

        break


def _show_report(posts: list, directory: Path, index: EmbeddingIndex) -> None:
    from hugin.scanner import collect_tag_pool, find_duplicate_tags
    from hugin.state import get_last_processed, load_state

    state = load_state(directory)

    no_tags = 0
    edited = 0
    up_to_date = 0

    for post in posts:
        last_processed = get_last_processed(state, post.filename)
        if not post.has_tags:
            no_tags += 1
        elif last_processed and post.lastmod and post.lastmod > last_processed:
            edited += 1
        else:
            up_to_date += 1

    pool = collect_tag_pool(posts)
    duplicates = find_duplicate_tags(pool)

    click.echo(f"Posts sem tags:              {no_tags}")
    click.echo(f"Posts editados apos tags:    {edited}")
    click.echo(f"Posts atualizados:           {up_to_date}")
    click.echo(f"Total:                       {len(posts)}")
    click.echo()
    click.echo(f"Tags unicas:                 {len(pool)}")

    if duplicates:
        click.echo()
        click.echo("Tags possivelmente duplicadas:")
        for tag_a, tag_b, reason in duplicates:
            click.echo(f"  {tag_a} <-> {tag_b}   ({reason})")

    # Embedding stats
    cached = index._cache.get("posts", {})
    click.echo()
    click.echo(f"Embeddings cached:           {len(cached)}")
    click.echo(f"Embeddings missing:          {len(posts) - len(cached)}")
```

- [ ] **Step 2: Run existing tests to make sure nothing broke**

Run: `pytest tests/ -v --ignore=tests/munin`
Expected: All tests PASS. CLI tests don't exist but imports in other modules should still resolve.

- [ ] **Step 3: Commit**

```bash
git add src/hugin/cli.py
git commit -m "Unify CLI: merge Munin init flow into hugin entry point"
```

---

### Task 5: Update HuginApp to accept new dependencies

**Files:**
- Modify: `src/hugin/tui/app.py`

- [ ] **Step 1: Rewrite app.py**

Replace the entire content of `src/hugin/tui/app.py`:

```python
"""Main Textual app."""

from pathlib import Path

from textual.app import App

from hugin.config import HuginConfig
from hugin.embeddings import EmbeddingIndex
from hugin.engines import Engine
from hugin.hugo import HugoSite
from hugin.scanner import Post


class HuginApp(App):
    """hugin main app."""

    TITLE = "hugin"

    def __init__(
        self,
        posts: list[Post],
        all_posts: list[Post],
        engine: Engine,
        pool: dict[str, int],
        state: dict,
        directory: Path,
        config: HuginConfig,
        site: HugoSite,
        index: EmbeddingIndex,
    ) -> None:
        super().__init__()
        self.posts = posts
        self.all_posts = all_posts
        self.engine = engine
        self.pool = pool
        self.state = state
        self.directory = directory
        self.config = config
        self.site = site
        self.index = index

    def on_mount(self) -> None:
        from hugin.tui.review import HuginScreen
        self.push_screen(HuginScreen(
            posts=self.posts,
            all_posts=self.all_posts,
            engine=self.engine,
            pool=self.pool,
            state=self.state,
            directory=self.directory,
            config=self.config,
            site=self.site,
            index=self.index,
        ))
```

- [ ] **Step 2: Commit**

```bash
git add src/hugin/tui/app.py
git commit -m "Update HuginApp to accept config, site, and index dependencies"
```

---

### Task 6: Create unified HuginScreen

This is the largest task. Fuses `ReviewScreen` (src/hugin/tui/review.py, 504 lines) and `MuninScreen` (src/munin/tui.py, ~989 lines) into one screen.

**Files:**
- Modify: `src/hugin/tui/review.py`

- [ ] **Step 1: Write the unified HuginScreen**

Replace the entire content of `src/hugin/tui/review.py`. The new file merges all functionality. Key changes from the originals:

- Class renamed from `ReviewScreen` to `HuginScreen`
- Keybindings merged: `t`, `s`, `i`, `o`, `l`, `u`, `e` (editor), `n` (engine), `m` (manage tags), `c` (clear caches), `q`, `escape`
- `ClickableLink` and `ConfirmClearScreen` widgets included (from munin/tui.py)
- `ConfirmStripLinksScreen` removed (strip-all removed per spec)
- Session state for incoming/outgoing is inline dict instead of separate SessionState class
- Metadata panel includes link counts (from MuninScreen)
- `action_editor()` pushes `EditorScreen` (Task 7)

```python
"""Unified Hugin screen — tags, summaries, links, and editing."""

import json
import math
import re
from datetime import datetime
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Input,
    Label,
    Static,
    TextArea,
)

from hugin.config import HuginConfig
from hugin.embeddings import EmbeddingIndex
from hugin.engines import Engine, load_engines, save_last_engine
from hugin.hugo import HugoSite
from hugin.linker import (
    _find_whole_word,
    apply_links,
    check_anchor_viable,
    count_internal_links,
    extract_existing_links,
    find_protected_zones,
    is_in_protected_zone,
    list_links,
    remove_specific_links,
    write_post_with_links,
)
from hugin.llm import (
    ANCHOR_SYSTEM_PROMPT,
    ANCHOR_USER_TEMPLATE,
    MAX_SUMMARY_CHARS,
    RETRY_PROMPT,
    SUGGEST_PROMPT,
    call_llm,
    parse_anchor_response,
    parse_suggestions,
    suggest_summary,
    suggest_tags,
)
from hugin.normalizer import normalize_tag, normalize_tags, strip_accents
from hugin.scanner import Post, format_pool_for_prompt
from hugin.state import mark_processed, save_state
from hugin.writer import write_summary, write_tags

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

STATE_BROWSING = "browsing"
STATE_LOADING = "loading"
STATE_REVIEWING = "reviewing"


class ClickableLink(Static, can_focus=True):
    """A focusable, clickable text that triggers navigation."""

    BINDINGS = [("enter", "activate", "Go")]

    DEFAULT_CSS = """
    ClickableLink {
        height: auto;
    }
    ClickableLink:focus {
        text-style: bold reverse;
    }
    """

    def __init__(self, text: str, post_index: int) -> None:
        super().__init__(f"[underline]{text}[/underline]")
        self.post_index = post_index

    def on_click(self) -> None:
        self._go()

    def action_activate(self) -> None:
        self._go()

    def _go(self) -> None:
        screen = self.screen
        if hasattr(screen, "_navigate_to_post"):
            screen._navigate_to_post(self.post_index)


class ConfirmClearScreen(ModalScreen[bool]):
    """Confirm cache clear and restart."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    ConfirmClearScreen {
        align: center middle;
    }

    #confirm-modal {
        width: 50;
        height: auto;
        border: solid $warning;
        background: $surface;
        padding: 1 2;
    }

    #confirm-modal Label {
        text-style: bold;
        margin-bottom: 1;
    }

    #confirm-buttons {
        height: auto;
        margin-top: 1;
    }

    #confirm-buttons Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-modal"):
            yield Label("Clear all caches and restart?")
            yield Static("Embedding cache will be rebuilt on restart.")
            with Horizontal(id="confirm-buttons"):
                yield Button("Clear & Restart", id="btn-confirm", variant="warning")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


class HuginScreen(Screen):
    """Unified screen: tags, summaries, links, editor."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("t", "tags", "Tags"),
        ("s", "summary", "Summary"),
        ("i", "incoming", "Incoming"),
        ("o", "outgoing", "Outgoing"),
        ("l", "list_links", "List"),
        ("u", "suggest", "Suggest"),
        ("e", "editor", "Editor"),
        ("n", "pick_engine", "Engine"),
        ("m", "manage_tags", "Manage"),
        ("c", "clear_caches", "Clear"),
        ("escape", "back", "Back"),
    ]

    DEFAULT_CSS = """
    #banner {
        height: auto;
        content-align: left middle;
        text-style: bold;
        color: $accent;
        padding: 0 1;
        margin-bottom: 1;
    }

    #review-container {
        height: 1fr;
    }

    #post-list-panel {
        width: 3fr;
        border-right: solid $accent;
    }

    #post-table {
        height: 1fr;
    }

    #detail-panel {
        width: 2fr;
        padding: 1;
        overflow-y: auto;
    }

    #detail-panel > * {
        height: auto;
    }

    #suggested-tags-container {
        height: auto;
    }

    #suggested-tags-container Checkbox {
        height: auto;
    }

    .outgoing-context {
        margin-left: 4;
        height: auto;
    }

    .outgoing-url {
        color: $text-muted;
        margin-left: 4;
        margin-bottom: 1;
    }

    .incoming-link {
        height: auto;
    }

    .section-label {
        margin-top: 1;
        text-style: bold;
    }

    #engine-label {
        color: $text-muted;
        margin-bottom: 1;
    }

    #manual-tags-input {
        margin-top: 1;
    }

    #review-buttons {
        height: auto;
        margin-top: 1;
    }

    .hidden {
        display: none;
    }
    """

    BANNER = """\
  _  _           _
 | || |_  _ __ _(_)_ _
 | __ | || / _` | | ' \\
 |_||_|\\_,_\\__, |_|_||_|
           |___/"""

    def __init__(
        self,
        posts: list[Post],
        all_posts: list[Post],
        engine: Engine,
        pool: dict[str, int],
        state: dict,
        directory: Path,
        config: HuginConfig,
        site: HugoSite,
        index: EmbeddingIndex,
    ) -> None:
        super().__init__()
        self.posts = posts
        self.all_posts = all_posts
        self.engine = engine
        self.pool = pool
        self.state = state
        self.directory = directory
        self.config = config
        self.site = site
        self.index = index
        self.current_index = 0
        self.suggested_tags: list[str] = []
        self._existing_checkboxes: list[Checkbox] = []
        self._suggested_checkboxes: list[Checkbox] = []
        self._outgoing_checkboxes: list[Checkbox] = []
        self._listed_links: list[dict] = []
        self._row_keys: list[str] = []
        self._spinner_frame = 0
        self._spinning_row: int | None = None
        self._done_rows: set[int] = set()
        self._spinner_timer: Timer | None = None
        self._state = STATE_BROWSING
        self._mode = ""  # "tags", "summary", "outgoing", "list"
        self._suggested_summary = ""
        self._suggested_topics: list[str] = []
        self._incoming_index: dict[str, int] = {}
        # Session cache for outgoing suggestions
        self._session_outgoing: dict[str, list[dict]] = {}

    def compose(self) -> ComposeResult:
        yield Static(self.BANNER, id="banner")

        with Horizontal(id="review-container"):
            with Vertical(id="post-list-panel"):
                table = DataTable(id="post-table", cursor_type="row", zebra_stripes=True)
                yield table

            with Vertical(id="detail-panel"):
                yield Static("", id="engine-label")
                yield Label(id="progress-label")
                yield Static("", id="post-meta")
                yield Label("", classes="section-label", id="section-header")
                yield Vertical(id="suggested-tags-container")
                yield Input(
                    placeholder="Manual tags (comma-separated)",
                    id="manual-tags-input",
                    classes="hidden",
                )
                with Horizontal(id="review-buttons", classes="hidden"):
                    yield Button("Apply", id="btn-apply", variant="primary")
                    yield Button("Skip", id="btn-skip")

        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#post-table", DataTable)
        table.add_column("", key="status", width=1)
        table.add_column("Post", key="title")

        from rich.text import Text
        for i, post in enumerate(self.posts):
            title = post.metadata.get("title", post.filename)
            if post.metadata.get("draft"):
                cell = Text(f"[DRAFT] {title}", style="dim")
            else:
                cell = Text(title)
            status = "—" if self.index.has_no_outgoing(post) else " "
            table.add_row(status, cell, key=f"post-{i}")
            self._row_keys.append(f"post-{i}")

        self._spinner_timer = self.set_interval(0.08, self._tick_spinner)
        self._build_incoming_index()
        self._update_engine_label()
        self._update_detail_panel()

    # --- Incoming index ---

    def _build_incoming_index(self) -> None:
        counts: dict[str, int] = {}
        for post in self.all_posts:
            for url in extract_existing_links(post.content):
                counts[url] = counts.get(url, 0) + 1
        self._incoming_index = counts

    # --- Spinner ---

    def _tick_spinner(self) -> None:
        if self._spinning_row is not None:
            self._spinner_frame += 1
            char = SPINNER_FRAMES[self._spinner_frame % len(SPINNER_FRAMES)]
            table = self.query_one("#post-table", DataTable)
            table.update_cell(self._row_keys[self._spinning_row], "status", char)

    def _start_spinner(self, index: int) -> None:
        self._spinning_row = index
        self._spinner_frame = 0

    def _stop_spinner(self, done: bool = False) -> None:
        if self._spinning_row is not None:
            table = self.query_one("#post-table", DataTable)
            if done:
                table.update_cell(self._row_keys[self._spinning_row], "status", "✓")
                self._done_rows.add(self._spinning_row)
            else:
                table.update_cell(self._row_keys[self._spinning_row], "status", " ")
            self._spinning_row = None

    def _mark_table_row(self, index: int, symbol: str) -> None:
        table = self.query_one("#post-table", DataTable)
        table.update_cell(self._row_keys[index], "status", symbol)

    # --- Engine ---

    def _update_engine_label(self) -> None:
        label = self.query_one("#engine-label", Static)
        label.update(f"Engine: {self.engine.id} ({self.engine.model})")

    def action_pick_engine(self) -> None:
        if self._state != STATE_BROWSING:
            return

        from hugin.tui.engine_picker import EnginePickerScreen
        engines = load_engines()

        def on_pick(engine: Engine | None) -> None:
            if engine is not None:
                self.engine = engine
                save_last_engine(engine)
                self._update_engine_label()
                self.notify(f"Engine: {engine.id} ({engine.model})")

        self.app.push_screen(EnginePickerScreen(engines, self.engine.id), on_pick)

    # --- Navigation ---

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self._state != STATE_BROWSING:
            return
        if event.cursor_row is not None and event.cursor_row != self.current_index:
            self.current_index = event.cursor_row
            self._update_detail_panel()

    def _navigate_to_post(self, index: int) -> None:
        if 0 <= index < len(self.posts):
            self.current_index = index
            self._state = STATE_BROWSING
            table = self.query_one("#post-table", DataTable)
            table.move_cursor(row=index)
            self._update_detail_panel()

    def _find_post_index(self, abs_path: str) -> int | None:
        for i, post in enumerate(self.posts):
            if str(post.path.resolve()) == abs_path:
                return i
        return None

    # --- Detail panel ---

    def _update_detail_panel(self) -> None:
        from rich.table import Table

        post = self.posts[self.current_index]
        total = len(self.posts)

        self.query_one("#progress-label", Label).update(
            f"{self.current_index + 1}/{total} — {post.filename}"
        )

        meta = post.metadata
        css_vars = self.app.get_css_variables()
        accent = self.app.current_theme.accent if hasattr(self.app, "current_theme") else "cyan"
        stripe_bg = css_vars.get("surface-darken-1", "#2d2d2d")

        table = Table(
            show_header=False, box=None, padding=(0, 1, 0, 0),
            row_styles=["", f"on {stripe_bg}"],
        )
        table.add_column("Field", style=f"bold {accent}", no_wrap=True)
        table.add_column("Value")

        skip_last = ("description", "tags")
        for key, value in meta.items():
            if key in skip_last:
                continue
            row = self._format_meta_value(value)
            if row is not None:
                table.add_row(key, row)

        desc = meta.get("description", "")
        if desc:
            table.add_row("description", f"{desc} ({len(str(desc))} chars)")
        tags = meta.get("tags")
        if tags:
            table.add_row("tags", ", ".join(str(t) for t in tags))
        elif tags is not None:
            table.add_row("tags", "(none)")

        # Link counts
        outgoing_count = len(extract_existing_links(post.content))
        word_count = len(post.content.split())
        budget = min(
            self.config.links.max_per_post,
            math.floor(word_count / self.config.links.words_per_link),
        )
        post_url = self.index.get_post_url(post)
        incoming_count = self._incoming_index.get(post_url, 0) if post_url else 0

        table.add_row("links out", f"{outgoing_count}/{budget}")
        table.add_row("links in", str(incoming_count))

        if budget == 0:
            table.add_row("outgoing", "[bold red]too short[/bold red]")
        elif self.index.has_no_outgoing(post):
            table.add_row("outgoing", "[dim]no opportunities[/dim]")

        self.query_one("#post-meta", Static).update(table)
        self._clear_action_area()

    @staticmethod
    def _format_meta_value(value) -> str | None:
        if value is None or value == "":
            return None
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return str(value)

    def _clear_action_area(self) -> None:
        self.suggested_tags = []
        self._suggested_summary = ""
        self._existing_checkboxes = []
        self._suggested_checkboxes = []
        self._outgoing_checkboxes = []
        self._listed_links = []
        self._suggested_topics = []
        self._mode = ""

        container = self.query_one("#suggested-tags-container")
        container.remove_children()
        self.query_one("#section-header", Label).update("")
        manual_input = self.query_one("#manual-tags-input", Input)
        manual_input.value = ""
        manual_input.add_class("hidden")
        self.query_one("#review-buttons").add_class("hidden")

    # --- Context extraction (for link display) ---

    @staticmethod
    def _extract_context(content: str, anchor: str, chars: int = 80) -> tuple[str, str]:
        pos = _find_whole_word(content, anchor)
        if pos == -1:
            pos = content.find(anchor)
        if pos == -1:
            return ("", "")

        start = max(0, pos - chars)
        end = min(len(content), pos + len(anchor) + chars)

        before = content[start:pos]
        after = content[pos + len(anchor):end]

        if start > 0:
            space = before.find(" ")
            if space != -1:
                before = before[space + 1:]
            before = "…" + before

        if end < len(content):
            space = after.rfind(" ")
            if space != -1:
                after = after[:space]
            after = after + "…"

        before = before.replace("\n", " ")
        after = after.replace("\n", " ")
        return (before, after)

    # === TAGS ===

    def action_tags(self) -> None:
        if self._state != STATE_BROWSING:
            return
        self._state = STATE_LOADING
        self._mode = "tags"
        post = self.posts[self.current_index]
        self._clear_action_area()
        self._mode = "tags"
        self._start_spinner(self.current_index)
        self._call_llm_tags(post)

    @work(exclusive=True)
    async def _call_llm_tags(self, post: Post) -> None:
        pool_str = format_pool_for_prompt(self.pool)
        try:
            raw_tags = await suggest_tags(
                self.engine, post.metadata, post.content, pool_str,
            )
            normalized = normalize_tags(raw_tags, post.tags, self.pool)
            self._display_tags(normalized)
        except Exception as e:
            self._display_error(self._format_error(e))

    def _display_tags(self, tags: list[str]) -> None:
        self._state = STATE_REVIEWING
        self._stop_spinner()
        self.suggested_tags = tags
        self._existing_checkboxes = []
        self._suggested_checkboxes = []

        container = self.query_one("#suggested-tags-container")
        container.remove_children()

        post = self.posts[self.current_index]

        if post.tags:
            container.mount(Label("Existing:", classes="section-label"))
            for tag in post.tags:
                cb = Checkbox(tag, value=True)
                self._existing_checkboxes.append(cb)
                container.mount(cb)

        if tags:
            container.mount(Label("Suggested:", classes="section-label"))
            existing_lower = {t.lower() for t in self.pool}
            for tag in tags:
                is_new = tag.lower() not in existing_lower
                label = f"✨ {tag}" if is_new else tag
                cb = Checkbox(label, value=True)
                self._suggested_checkboxes.append(cb)
                container.mount(cb)

        self.query_one("#section-header", Label).update("")
        self.query_one("#manual-tags-input", Input).remove_class("hidden")
        self.query_one("#review-buttons").remove_class("hidden")
        self.query_one("#btn-apply", Button).label = "Apply"
        self.query_one("#btn-apply", Button).focus()

    def _apply_tags(self) -> None:
        post = self.posts[self.current_index]

        kept = [cb.label.plain for cb in self._existing_checkboxes if cb.value]
        added = [
            cb.label.plain.removeprefix("✨ ")
            for cb in self._suggested_checkboxes if cb.value
        ]
        removed = [cb.label.plain for cb in self._existing_checkboxes if not cb.value]

        manual_raw = self.query_one("#manual-tags-input", Input).value
        manual = [
            normalize_tag(t, strip_articles=False)
            for t in manual_raw.split(",")
            if t.strip()
        ]
        manual = [t for t in manual if t]

        final_tags = kept + added + manual

        write_tags(post.path, final_tags)
        mark_processed(self.state, post.filename)
        save_state(self.directory, self.state)

        for tag in removed:
            if tag in self.pool:
                self.pool[tag] = max(0, self.pool[tag] - 1)
        for tag in added + manual:
            self.pool[tag] = self.pool.get(tag, 0) + 1

        post.tags = final_tags
        post.metadata["tags"] = final_tags
        post.metadata["lastmod"] = datetime.now().isoformat(timespec="seconds")
        self._stop_spinner(done=True)

        parts = []
        if added:
            parts.append(f"+{len(added)}")
        if manual:
            parts.append(f"+{len(manual)} manual")
        if removed:
            parts.append(f"-{len(removed)}")
        msg = ", ".join(parts) if parts else "no changes"
        self.notify(f"{post.filename}: {msg}")

        self._state = STATE_BROWSING
        self._update_detail_panel()
        self.query_one("#post-table", DataTable).focus()

    # === SUMMARY ===

    def action_summary(self) -> None:
        if self._state != STATE_BROWSING:
            return
        self._state = STATE_LOADING
        self._mode = "summary"
        post = self.posts[self.current_index]
        self._clear_action_area()
        self._mode = "summary"
        self._start_spinner(self.current_index)
        self._call_llm_summary(post)

    @work(exclusive=True)
    async def _call_llm_summary(self, post: Post) -> None:
        try:
            summary = await suggest_summary(
                self.engine, post.metadata, post.content,
            )
            self._display_summary(summary)
        except Exception as e:
            self._display_error(self._format_error(e))

    def _display_summary(self, summary: str) -> None:
        self._state = STATE_REVIEWING
        self._stop_spinner()
        self._suggested_summary = summary

        container = self.query_one("#suggested-tags-container")
        container.remove_children()

        post = self.posts[self.current_index]
        current_desc = post.metadata.get("description", "")

        if current_desc:
            container.mount(Label("[dim]Current:[/dim]", classes="section-label"))
            container.mount(Static(""))
            container.mount(Static(f"[dim]{current_desc} ({len(str(current_desc))} chars)[/dim]"))

        container.mount(Label("Edit summary:", classes="section-label"))
        ta = TextArea(summary, id="summary-editor")
        ta.styles.height = "auto"
        ta.styles.min_height = 3
        ta.styles.max_height = 6
        container.mount(ta)
        container.mount(Static(f"({len(summary)} chars)", id="summary-char-count", classes="meta-desc"))

        self.query_one("#section-header", Label).update("")
        self.query_one("#review-buttons").remove_class("hidden")
        self.query_one("#btn-apply", Button).label = "Apply"
        ta.focus()
        self.query_one("#btn-apply", Button).focus()

    def _apply_summary(self) -> None:
        post = self.posts[self.current_index]

        try:
            ta = self.query_one("#summary-editor", TextArea)
            final_summary = ta.text.strip()
        except Exception:
            final_summary = self._suggested_summary

        if not final_summary:
            self.notify("Summary is empty.", severity="warning")
            return

        write_summary(post.path, final_summary)
        post.metadata["description"] = final_summary
        post.metadata["lastmod"] = datetime.now().isoformat(timespec="seconds")
        self._stop_spinner(done=True)
        self.notify(f"{post.filename}: summary updated")

        self._state = STATE_BROWSING
        self._update_detail_panel()
        self.query_one("#post-table", DataTable).focus()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "summary-editor":
            try:
                counter = self.query_one("#summary-char-count", Static)
                chars = len(event.text_area.text.strip())
                if chars > MAX_SUMMARY_CHARS:
                    counter.update(f"[bold red]({chars} chars — over {MAX_SUMMARY_CHARS})[/bold red]")
                else:
                    counter.update(f"({chars} chars)")
            except Exception:
                pass

    # === INCOMING LINKS ===

    def action_incoming(self) -> None:
        if self._state != STATE_BROWSING:
            return

        self._clear_action_area()
        post = self.posts[self.current_index]
        existing_urls = extract_existing_links(post.content)
        results = self.index.find_similar(
            post,
            n=self.config.links.candidates,
            exclude_urls=existing_urls,
        )
        self._show_incoming(results)
        self.notify(f"{len(results)} potential incoming links found")

    def _show_incoming(self, results: list[dict]) -> None:
        container = self.query_one("#suggested-tags-container")
        container.remove_children()
        header = self.query_one("#section-header", Label)

        if not results:
            header.update("")
            return

        header.update("Possible incoming links:")
        for item in results:
            post_index = self._find_post_index(item["path"])
            if post_index is not None:
                link = ClickableLink(item["title"], post_index)
                link.add_class("incoming-link")
                container.mount(link)
            else:
                container.mount(Static(item["title"]))

    # === OUTGOING LINKS ===

    def action_outgoing(self) -> None:
        if self._state != STATE_BROWSING:
            return

        self._clear_action_area()
        post = self.posts[self.current_index]
        word_count = len(post.content.split())
        budget = min(
            self.config.links.max_per_post,
            math.floor(word_count / self.config.links.words_per_link),
        )

        if budget == 0:
            self.notify("Post too short for link suggestions under current policy.")
            return

        self._state = STATE_LOADING
        self._mode = "outgoing"
        self._start_spinner(self.current_index)
        self.query_one("#section-header", Label).update("Querying LLM...")
        self._run_outgoing(post, budget)

    @work(exclusive=True)
    async def _run_outgoing(self, post: Post, budget: int) -> None:
        try:
            existing_urls = extract_existing_links(post.content)
            candidates = self.index.find_similar(
                post,
                n=self.config.links.candidates,
                exclude_urls=existing_urls,
            )

            if not candidates:
                self._stop_spinner()
                self._state = STATE_BROWSING
                self.notify("No similar posts found.")
                self.query_one("#section-header", Label).update("")
                return

            candidates_json = json.dumps([
                {"title": c["title"], "summary": "", "url": c["url"]}
                for c in candidates
            ])

            user_msg = ANCHOR_USER_TEMPLATE.format(
                body=post.content, candidates_json=candidates_json,
            )
            system = ANCHOR_SYSTEM_PROMPT.format(
                max_anchor_words=self.config.links.max_anchor_words,
            )
            prompt = f"{system}\n\n{user_msg}"
            response = await call_llm(self.engine, prompt)

            suggestions = parse_anchor_response(response)

            validated = []
            zones = find_protected_zones(post.content)

            for s in suggestions:
                anchor = s.get("anchor_text", "")
                target = s.get("target_url", "")
                if not anchor or not target:
                    continue

                max_words = self.config.links.max_anchor_words
                if len(anchor.split()) > max_words:
                    continue

                pos = _find_whole_word(post.content, anchor)
                if pos == -1:
                    candidate_info = next(
                        (c for c in candidates if c["url"] == target), None,
                    )
                    if candidate_info:
                        retry_prompt = RETRY_PROMPT.format(
                            anchor_text=anchor,
                            title=candidate_info["title"],
                            url=target,
                            body=post.content,
                        )
                        retry_response = await call_llm(self.engine, retry_prompt)
                        retry_anchor = retry_response.strip().strip('"').strip("'")
                        retry_pos = _find_whole_word(post.content, retry_anchor)
                        if retry_pos != -1 and len(retry_anchor.split()) <= max_words:
                            anchor = retry_anchor
                            pos = retry_pos
                        else:
                            continue
                    else:
                        continue

                if is_in_protected_zone(pos, len(anchor), zones):
                    continue

                validated.append({"anchor_text": anchor, "target_url": target})

            validated = validated[:budget]
            validated = [
                s for s in validated
                if check_anchor_viable(
                    post.content, s["anchor_text"],
                    self.config.links.max_per_paragraph,
                )
            ]

            abs_path = str(post.path.resolve())
            self._session_outgoing[abs_path] = validated

            self._stop_spinner()
            self._state = STATE_REVIEWING if validated else STATE_BROWSING
            self._mode = "outgoing" if validated else ""
            self._show_outgoing(validated)

            if validated:
                self.notify(f"{len(validated)} link suggestions ready")
            else:
                self.index.mark_no_outgoing(post)
                self._mark_table_row(self.current_index, "—")
                self.notify("No natural anchors found for this post.")

        except Exception as e:
            self._stop_spinner()
            self._state = STATE_BROWSING
            self.notify(f"Error: {e}", severity="error")
            self.query_one("#section-header", Label).update("")

    def _show_outgoing(self, suggestions: list[dict]) -> None:
        post = self.posts[self.current_index]
        container = self.query_one("#suggested-tags-container")
        container.remove_children()
        header = self.query_one("#section-header", Label)
        self._outgoing_checkboxes = []

        if not suggestions:
            header.update("")
            self.query_one("#review-buttons").add_class("hidden")
            return

        header.update("Outgoing link suggestions:")
        for item in suggestions:
            anchor = item["anchor_text"]
            url = item["target_url"]
            before, after = self._extract_context(post.content, anchor)
            cb = Checkbox(anchor, value=False)
            self._outgoing_checkboxes.append(cb)
            container.mount(cb)
            context_text = f"[dim]{before}[/dim][bold reverse]{anchor}[/bold reverse][dim]{after}[/dim]"
            container.mount(Static(context_text, classes="outgoing-context"))
            container.mount(Static(f"→ {url}", classes="outgoing-url"))

        self.query_one("#btn-apply", Button).label = "Insert links"
        self.query_one("#review-buttons").remove_class("hidden")

    def _apply_outgoing(self) -> None:
        if not self._outgoing_checkboxes:
            self.notify("No suggestions to apply.")
            return

        post = self.posts[self.current_index]
        abs_path = str(post.path.resolve())
        cached = self._session_outgoing.get(abs_path, [])
        selected = []
        for cb, suggestion in zip(self._outgoing_checkboxes, cached):
            if cb.value:
                selected.append(suggestion)

        if not selected:
            self.notify("No links selected.")
            return

        body, skipped = apply_links(
            post.content, selected,
            max_per_paragraph=self.config.links.max_per_paragraph,
        )

        write_post_with_links(post.path, body)

        post.content = body
        post.metadata["lastmod"] = datetime.now().isoformat(timespec="seconds")
        self.index.update_post(post, self.site.post_url)
        self._build_incoming_index()
        self._session_outgoing.pop(abs_path, None)

        self._stop_spinner(done=True)

        msg = f"{len(selected) - len(skipped)} links applied"
        if skipped:
            msg += f", {len(skipped)} skipped"
        self.notify(f"{post.filename}: {msg}")

        self._state = STATE_BROWSING
        self._update_detail_panel()

    # === LIST LINKS ===

    def action_list_links(self) -> None:
        if self._state != STATE_BROWSING:
            return

        self._clear_action_area()
        post = self.posts[self.current_index]
        links = list_links(post.content)

        if not links:
            self.notify("No links in this post.")
            return

        self._state = STATE_REVIEWING
        self._mode = "list"
        self._show_existing_links(links, post)

    def _show_existing_links(self, links: list[dict], post: Post) -> None:
        container = self.query_one("#suggested-tags-container")
        container.remove_children()
        header = self.query_one("#section-header", Label)
        self._outgoing_checkboxes = []
        self._listed_links = links

        header.update(f"Links ({len(links)}) — check to remove:")
        for link in links:
            anchor = link["anchor_text"]
            url = link["url"]
            before, after = self._extract_context(post.content, f"[{anchor}]({url})")
            cb = Checkbox(anchor, value=False)
            self._outgoing_checkboxes.append(cb)
            container.mount(cb)
            context_text = f"[dim]{before}[/dim][bold reverse]{anchor}[/bold reverse][dim]{after}[/dim]"
            container.mount(Static(context_text, classes="outgoing-context"))
            container.mount(Static(f"→ {url}", classes="outgoing-url"))

        self.query_one("#btn-apply", Button).label = "Remove links"
        self.query_one("#review-buttons").remove_class("hidden")

    def _do_remove_selected(self) -> None:
        post = self.posts[self.current_index]
        urls_to_remove = set()

        for cb, link in zip(self._outgoing_checkboxes, self._listed_links):
            if cb.value:
                urls_to_remove.add(link["url"])

        if not urls_to_remove:
            self.notify("No links selected for removal.")
            return

        body, removed = remove_specific_links(post.content, urls_to_remove)
        write_post_with_links(post.path, body)
        post.content = body
        post.metadata["lastmod"] = datetime.now().isoformat(timespec="seconds")
        self.index.update_post(post, self.site.post_url)
        self._build_incoming_index()

        self._state = STATE_BROWSING
        self._update_detail_panel()
        self.query_one("#post-table", DataTable).focus()
        self.notify(f"{post.filename}: {removed} links removed")

    # === SUGGEST TOPICS ===

    def action_suggest(self) -> None:
        if self._state != STATE_BROWSING:
            return

        self._state = STATE_LOADING
        post = self.posts[self.current_index]
        self._clear_action_area()
        self.query_one("#section-header", Label).update("Querying LLM...")
        self._start_spinner(self.current_index)
        self._run_suggest(post)

    @work(exclusive=True)
    async def _run_suggest(self, post: Post) -> None:
        try:
            prompt = SUGGEST_PROMPT.format(
                title=post.metadata.get("title", post.filename),
                content=post.content,
            )
            response = await call_llm(self.engine, prompt)
            suggestions = parse_suggestions(response)

            if not suggestions:
                self._stop_spinner()
                self._state = STATE_BROWSING
                self.notify("No suggestions generated.")
                return

            novel = []
            for title in suggestions:
                if not self._topic_exists(title):
                    novel.append(title)

            self._stop_spinner()
            self._state = STATE_BROWSING
            self._show_suggestions(novel, len(suggestions))

        except Exception as e:
            self._stop_spinner()
            self._state = STATE_BROWSING
            self.notify(f"Error: {e}", severity="error")

    def _topic_exists(self, suggested_title: str, threshold: float = 0.75) -> bool:
        cached = self.index._cache.get("posts", {})
        if not cached:
            return False

        import numpy as np

        suggested_lower = strip_accents(suggested_title.lower())
        for entry in cached.values():
            existing_lower = strip_accents(entry.get("title", "").lower())
            if suggested_lower in existing_lower or existing_lower in suggested_lower:
                return True

        if self.index._model is None:
            return False

        suggested_vec = self.index._encode_single(suggested_title)
        for entry in cached.values():
            other_vec = np.array(entry["embedding"])
            dot = np.dot(suggested_vec, other_vec)
            norm = np.linalg.norm(suggested_vec) * np.linalg.norm(other_vec)
            score = float(dot / norm) if norm > 0 else 0.0
            if score >= threshold:
                return True

        return False

    def _show_suggestions(self, novel: list[str], total: int) -> None:
        container = self.query_one("#suggested-tags-container")
        container.remove_children()
        header = self.query_one("#section-header", Label)

        if not novel:
            header.update("All suggested topics already covered!")
            self.notify(f"{total} topics suggested, all already exist in the blog.")
            return

        filtered = total - len(novel)
        if filtered > 0:
            header.update(f"New post ideas ({filtered} already covered, filtered out):")
        else:
            header.update("New post ideas:")

        self._suggested_topics = novel
        for title in novel:
            container.mount(Static(f"  • {title}"))

        container.mount(Button("Copy to clipboard", id="btn-copy-suggestions"))
        self.notify(f"{len(novel)} new topic ideas")

    @staticmethod
    def _copy_to_clipboard(text: str) -> None:
        import base64
        import sys
        encoded = base64.b64encode(text.encode()).decode()
        sys.stdout.write(f"\033]52;c;{encoded}\a")
        sys.stdout.flush()

    # === EDITOR ===

    def action_editor(self) -> None:
        if self._state != STATE_BROWSING:
            return

        from hugin.tui.editor import EditorScreen

        post = self.posts[self.current_index]

        def on_return(saved: bool) -> None:
            if saved:
                # Reload post from disk
                import frontmatter as fm
                updated = fm.load(str(post.path))
                post.metadata = updated.metadata
                post.content = updated.content
                post.tags = list(updated.metadata.get("tags", []) or [])
                post.has_tags = bool(post.tags)
                self.index.update_post(post, self.site.post_url)
                self._build_incoming_index()
            self._update_detail_panel()

        self.app.push_screen(
            EditorScreen(post=post),
            on_return,
        )

    # === MANAGE TAGS ===

    def action_manage_tags(self) -> None:
        if self._state != STATE_BROWSING:
            return

        from hugin.tui.tag_manager import TagManagerScreen

        def on_return(_=None) -> None:
            self._update_detail_panel()

        self.app.push_screen(
            TagManagerScreen(
                all_posts=self.all_posts,
                pool=self.pool,
                directory=self.directory,
            ),
            on_return,
        )

    # === CLEAR CACHES ===

    def action_clear_caches(self) -> None:
        if self._state != STATE_BROWSING:
            return

        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                self.index.clear_cache()
                self.app.exit(return_code=42)

        self.app.push_screen(ConfirmClearScreen(), on_confirm)

    # === BUTTON DISPATCH ===

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-apply":
            if self._mode == "tags":
                self._apply_tags()
            elif self._mode == "summary":
                self._apply_summary()
            elif self._mode == "list":
                self._do_remove_selected()
            elif self._mode == "outgoing":
                self._apply_outgoing()
        elif event.button.id == "btn-skip":
            self._state = STATE_BROWSING
            self._update_detail_panel()
            self.query_one("#post-table", DataTable).focus()
        elif event.button.id == "btn-copy-suggestions":
            if self._suggested_topics:
                text = "\n".join(f"• {t}" for t in self._suggested_topics)
                self._copy_to_clipboard(text)
                self.notify(f"{len(self._suggested_topics)} topics copied to clipboard")

    # === ERROR HANDLING ===

    @staticmethod
    def _format_error(e: Exception) -> str:
        import httpx
        if isinstance(e, httpx.ConnectError):
            return f"Connection refused — is the engine running? ({e.request.url})"
        if isinstance(e, httpx.ReadTimeout):
            return "Timeout — model took too long to respond"
        if isinstance(e, httpx.HTTPStatusError):
            code = e.response.status_code
            try:
                body = e.response.json().get("error", {}).get("message", "")
            except Exception:
                body = e.response.text[:200]
            return f"HTTP {code}: {body}"
        if isinstance(e, ValueError):
            return f"Bad LLM response: {e}"
        return f"{type(e).__name__}: {e}"

    def _display_error(self, error: str) -> None:
        self._state = STATE_BROWSING
        self._stop_spinner()
        self.notify(f"Error: {error}", severity="error")

    # === NAVIGATION ===

    def action_back(self) -> None:
        if self._state == STATE_REVIEWING:
            self._stop_spinner()
            self._state = STATE_BROWSING
            self._update_detail_panel()

    def action_quit(self) -> None:
        if self._spinner_timer:
            self._spinner_timer.stop()
        self.app.exit()
```

- [ ] **Step 2: Run all tests to verify nothing broke**

Run: `pytest tests/ -v --ignore=tests/munin`
Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add src/hugin/tui/review.py
git commit -m "Create unified HuginScreen merging tags, summaries, and links"
```

---

### Task 7: Create EditorScreen

**Files:**
- Create: `src/hugin/tui/editor.py`

- [ ] **Step 1: Write EditorScreen**

Create `src/hugin/tui/editor.py`:

```python
"""Built-in Markdown editor with frontmatter field editing."""

import os
import tempfile
from datetime import datetime

import frontmatter

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Footer,
    Input,
    Label,
    Static,
    TextArea,
)

from hugin.scanner import Post


# Fields that get dedicated Input widgets (order matters for display)
EDITABLE_FIELDS = ("title", "date", "lastmod", "draft", "slug", "url", "description")
# Fields managed by other tools — show but don't edit here
READONLY_FIELDS = ("tags",)


class ConfirmDiscardScreen(ModalScreen[bool]):
    """Confirm discarding unsaved changes."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    ConfirmDiscardScreen {
        align: center middle;
    }

    #discard-modal {
        width: 50;
        height: auto;
        border: solid $warning;
        background: $surface;
        padding: 1 2;
    }

    #discard-modal Label {
        text-style: bold;
        margin-bottom: 1;
    }

    #discard-buttons {
        height: auto;
        margin-top: 1;
    }

    #discard-buttons Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="discard-modal"):
            yield Label("Discard unsaved changes?")
            yield Static("Your edits will be lost.")
            with Horizontal(id="discard-buttons"):
                yield Button("Discard", id="btn-discard", variant="warning")
                yield Button("Cancel", id="btn-cancel-discard")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-discard")

    def action_cancel(self) -> None:
        self.dismiss(False)


class EditorScreen(Screen[bool]):
    """Full-screen editor for post frontmatter and body."""

    BINDINGS = [
        ("ctrl+s", "save", "Save"),
        ("escape", "back", "Back"),
    ]

    DEFAULT_CSS = """
    #editor-container {
        height: 1fr;
    }

    #frontmatter-panel {
        height: auto;
        max-height: 40%;
        padding: 1;
        border-bottom: solid $accent;
    }

    .field-row {
        height: auto;
        margin-bottom: 0;
    }

    .field-label {
        width: 15;
        text-style: bold;
        padding-top: 1;
    }

    .field-input {
        width: 1fr;
    }

    .field-readonly {
        width: 1fr;
        color: $text-muted;
        padding-top: 1;
    }

    #body-panel {
        height: 1fr;
        padding: 0 1;
    }

    #body-editor {
        height: 1fr;
    }

    #editor-buttons {
        height: auto;
        padding: 1;
        dock: bottom;
    }

    #editor-buttons Button {
        margin: 0 1;
    }

    #editor-title {
        text-style: bold;
        padding: 1;
        color: $accent;
    }
    """

    def __init__(self, post: Post) -> None:
        super().__init__()
        self.post = post
        self._field_inputs: dict[str, Input] = {}
        self._extra_fields: dict[str, str] = {}
        self._original_meta = dict(post.metadata)
        self._original_content = post.content

    def compose(self) -> ComposeResult:
        yield Static(f"Editing: {self.post.filename}", id="editor-title")

        with Vertical(id="editor-container"):
            with Vertical(id="frontmatter-panel"):
                meta = self.post.metadata

                # Editable fields
                for field_name in EDITABLE_FIELDS:
                    value = meta.get(field_name, "")
                    if value is None:
                        value = ""
                    if isinstance(value, list):
                        value = ", ".join(str(v) for v in value)
                    else:
                        value = str(value) if value else ""

                    with Horizontal(classes="field-row"):
                        yield Label(field_name, classes="field-label")
                        inp = Input(value=value, id=f"field-{field_name}", classes="field-input")
                        self._field_inputs[field_name] = inp
                        yield inp

                # Read-only fields
                for field_name in READONLY_FIELDS:
                    value = meta.get(field_name, "")
                    if isinstance(value, list):
                        display = ", ".join(str(v) for v in value)
                    else:
                        display = str(value) if value else ""

                    with Horizontal(classes="field-row"):
                        yield Label(field_name, classes="field-label")
                        yield Static(f"[dim]{display}[/dim]", classes="field-readonly")

                # Track extra fields not in EDITABLE_FIELDS or READONLY_FIELDS
                known = set(EDITABLE_FIELDS) | set(READONLY_FIELDS)
                for key, value in meta.items():
                    if key not in known:
                        if isinstance(value, list):
                            self._extra_fields[key] = ", ".join(str(v) for v in value)
                        else:
                            self._extra_fields[key] = str(value) if value is not None else ""

            with Vertical(id="body-panel"):
                yield TextArea(
                    self.post.content,
                    language="markdown",
                    id="body-editor",
                )

        with Horizontal(id="editor-buttons"):
            yield Button("Save (Ctrl+S)", id="btn-save", variant="primary")
            yield Button("Cancel", id="btn-cancel-edit")

        yield Footer()

    def _is_dirty(self) -> bool:
        """Check if anything has been modified."""
        # Check frontmatter fields
        for field_name, inp in self._field_inputs.items():
            original = self._original_meta.get(field_name, "")
            if original is None:
                original = ""
            if isinstance(original, list):
                original = ", ".join(str(v) for v in original)
            else:
                original = str(original) if original else ""
            if inp.value != original:
                return True

        # Check body
        try:
            body_editor = self.query_one("#body-editor", TextArea)
            if body_editor.text != self._original_content:
                return True
        except Exception:
            pass

        return False

    def action_save(self) -> None:
        """Save the post with updated frontmatter and body."""
        post = self.post

        # Build new metadata starting from original (preserves order and extra fields)
        new_meta = dict(self._original_meta)

        # Update editable fields from inputs
        for field_name, inp in self._field_inputs.items():
            value = inp.value.strip()
            if not value:
                # Remove empty fields (except title)
                if field_name != "title" and field_name in new_meta:
                    del new_meta[field_name]
                continue

            # Preserve original types where possible
            original = self._original_meta.get(field_name)
            if field_name == "draft":
                new_meta[field_name] = value.lower() in ("true", "1", "yes")
            elif field_name == "tags":
                new_meta[field_name] = [t.strip() for t in value.split(",") if t.strip()]
            else:
                new_meta[field_name] = value

        # Get body content
        body_editor = self.query_one("#body-editor", TextArea)
        new_content = body_editor.text

        # Update lastmod
        new_meta["lastmod"] = datetime.now().isoformat(timespec="seconds")

        # Build post object for frontmatter lib
        fm_post = frontmatter.Post(new_content, **new_meta)

        # Atomic write
        dir_path = post.path.parent
        fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".md")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(frontmatter.dumps(fm_post, sort_keys=False))
                f.write("\n")
            os.replace(tmp_path, str(post.path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        self.notify(f"{post.filename}: saved")
        self.dismiss(True)

    def action_back(self) -> None:
        """Go back, confirming if dirty."""
        if self._is_dirty():
            def on_confirm(discard: bool) -> None:
                if discard:
                    self.dismiss(False)

            self.app.push_screen(ConfirmDiscardScreen(), on_confirm)
        else:
            self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save":
            self.action_save()
        elif event.button.id == "btn-cancel-edit":
            self.action_back()
```

- [ ] **Step 2: Run all tests**

Run: `pytest tests/ -v --ignore=tests/munin`
Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add src/hugin/tui/editor.py
git commit -m "Add EditorScreen for in-place frontmatter and body editing"
```

---

### Task 8: Delete munin package and update pyproject.toml

**Files:**
- Delete: `src/munin/` (entire directory)
- Delete: `tests/munin/` (entire directory)
- Modify: `pyproject.toml`

- [ ] **Step 1: Remove munin entry point from pyproject.toml**

In `pyproject.toml`, change:

```toml
# OLD
[project.scripts]
hugin = "hugin.cli:main"
munin = "munin.cli:main"

# NEW
[project.scripts]
hugin = "hugin.cli:main"
```

- [ ] **Step 2: Delete munin source and tests**

```bash
rm -rf src/munin/
rm -rf tests/munin/
```

- [ ] **Step 3: Delete legacy selection.py (unused)**

```bash
rm src/hugin/tui/selection.py
```

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests PASS. No imports from `munin` remain.

- [ ] **Step 5: Verify no stale imports**

```bash
grep -r "from munin" src/ tests/
grep -r "import munin" src/ tests/
```

Expected: No output (no references to old package).

- [ ] **Step 6: Reinstall package**

```bash
pip install -e .
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Remove munin package, single unified hugin command"
```

---

### Task 9: Final smoke test

- [ ] **Step 1: Run full test suite one more time**

Run: `pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 2: Verify hugin command starts**

Run: `hugin --help`
Expected: Shows help with `--batch`, `--report`, `--engine`, `--model` options.

- [ ] **Step 3: Verify munin command is gone**

Run: `munin --help`
Expected: Command not found (after reinstall).

- [ ] **Step 4: Commit (if any fixups needed)**

Only if previous steps revealed issues that needed fixing.
