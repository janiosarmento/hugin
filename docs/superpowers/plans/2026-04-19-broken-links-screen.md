# Broken Internal Links Screen — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a ModalScreen to Hugin that scans all posts for internal links pointing to draft or non-existent posts, and allows bulk removal of selected broken links.

**Architecture:** New `find_broken_links()` function in `linker.py` builds a URL→Post map from all posts, then checks every internal link against it. New `BrokenLinksScreen` ModalScreen in `broken_links.py` displays results in a DataTable with row-toggle selection and a "Remove selected" button that groups removals by source post.

**Tech Stack:** Python, Textual (ModalScreen, DataTable, Button), existing `linker.py` utilities (`list_links`, `remove_specific_links`, `write_post_with_links`).

**Spec:** `docs/superpowers/specs/2026-04-19-broken-links-screen-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/hugin/linker.py` | Modify | Add `BrokenLink` dataclass and `find_broken_links()` function |
| `tests/test_linker.py` | Modify | Add tests for `find_broken_links()` |
| `src/hugin/tui/broken_links.py` | Create | `BrokenLinksScreen` ModalScreen |
| `src/hugin/tui/review.py` | Modify | Add `b` binding and `action_broken_links()` |

---

### Task 1: Add `find_broken_links()` to linker.py

**Files:**
- Modify: `src/hugin/linker.py`
- Modify: `tests/test_linker.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_linker.py`:

```python
from dataclasses import dataclass
from hugin.linker import find_broken_links, BrokenLink
from hugin.scanner import Post
from hugin.hugo import HugoSite


class TestFindBrokenLinks:
    def _make_post(self, tmp_path, filename, content, metadata=None):
        """Helper to create a Post with a real file."""
        if metadata is None:
            metadata = {}
        path = tmp_path / filename
        # Build frontmatter string
        import yaml
        fm = yaml.dump(metadata, default_flow_style=False).strip()
        path.write_text(f"---\n{fm}\n---\n\n{content}\n")
        tags = metadata.get("tags", []) or []
        return Post(
            path=path,
            metadata=metadata,
            content=content,
            has_tags=bool(tags),
            tags=list(tags),
        )

    def _make_site(self, tmp_path):
        """Create a minimal HugoSite for testing."""
        posts_dir = tmp_path / "content" / "posts"
        posts_dir.mkdir(parents=True)
        (tmp_path / "hugo.toml").write_text(
            '[permalinks]\nposts = "/posts/:slug/"'
        )
        return HugoSite(posts_dir)

    def test_link_to_nonexistent_post(self, tmp_path):
        site = self._make_site(tmp_path)
        post_a = self._make_post(
            tmp_path, "post-a.md",
            "Check [this](/posts/does-not-exist/) out.",
            {"title": "Post A"},
        )
        result = find_broken_links([post_a], site)
        assert len(result) == 1
        assert result[0].target_url == "/posts/does-not-exist/"
        assert result[0].reason == "not_found"
        assert result[0].anchor_text == "this"

    def test_link_to_draft_post(self, tmp_path):
        site = self._make_site(tmp_path)
        post_a = self._make_post(
            tmp_path, "post-a.md",
            "See [drafty](/posts/draft-post/) for more.",
            {"title": "Post A"},
        )
        post_draft = self._make_post(
            tmp_path, "draft-post.md",
            "Draft content.",
            {"title": "Draft Post", "draft": True},
        )
        result = find_broken_links([post_a, post_draft], site)
        assert len(result) == 1
        assert result[0].target_url == "/posts/draft-post/"
        assert result[0].reason == "draft"

    def test_link_to_published_post_is_ok(self, tmp_path):
        site = self._make_site(tmp_path)
        post_a = self._make_post(
            tmp_path, "post-a.md",
            "See [good link](/posts/post-b/) here.",
            {"title": "Post A"},
        )
        post_b = self._make_post(
            tmp_path, "post-b.md",
            "I exist and am published.",
            {"title": "Post B"},
        )
        result = find_broken_links([post_a, post_b], site)
        assert len(result) == 0

    def test_external_links_ignored(self, tmp_path):
        site = self._make_site(tmp_path)
        post_a = self._make_post(
            tmp_path, "post-a.md",
            "Visit [example](https://example.com) and [broken](/posts/nope/).",
            {"title": "Post A"},
        )
        result = find_broken_links([post_a], site)
        assert len(result) == 1
        assert result[0].target_url == "/posts/nope/"

    def test_multiple_broken_links_in_one_post(self, tmp_path):
        site = self._make_site(tmp_path)
        post_a = self._make_post(
            tmp_path, "post-a.md",
            "See [a](/posts/nope1/) and [b](/posts/nope2/) here.",
            {"title": "Post A"},
        )
        result = find_broken_links([post_a], site)
        assert len(result) == 2
        urls = {r.target_url for r in result}
        assert urls == {"/posts/nope1/", "/posts/nope2/"}

    def test_no_broken_links_returns_empty(self, tmp_path):
        site = self._make_site(tmp_path)
        post_a = self._make_post(
            tmp_path, "post-a.md",
            "No links here, just plain text.",
            {"title": "Post A"},
        )
        result = find_broken_links([post_a], site)
        assert len(result) == 0

    def test_draft_linking_to_draft_is_broken(self, tmp_path):
        site = self._make_site(tmp_path)
        draft_a = self._make_post(
            tmp_path, "draft-a.md",
            "See [other draft](/posts/draft-b/).",
            {"title": "Draft A", "draft": True},
        )
        draft_b = self._make_post(
            tmp_path, "draft-b.md",
            "Also a draft.",
            {"title": "Draft B", "draft": True},
        )
        result = find_broken_links([draft_a, draft_b], site)
        assert len(result) == 1
        assert result[0].reason == "draft"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_linker.py::TestFindBrokenLinks -v`
Expected: FAIL with `ImportError: cannot import name 'find_broken_links'`

- [ ] **Step 3: Implement `BrokenLink` and `find_broken_links()` in linker.py**

Add these imports at the top of `src/hugin/linker.py`:

```python
from dataclasses import dataclass
```

Add the `TYPE_CHECKING` block after existing imports:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hugin.scanner import Post
    from hugin.hugo import HugoSite
```

Add at the end of `src/hugin/linker.py`:

```python
@dataclass
class BrokenLink:
    """A link in a post that points to a draft or non-existent post."""

    source_post: Post
    anchor_text: str
    target_url: str
    reason: str  # "draft" or "not_found"


def find_broken_links(all_posts: list[Post], site: HugoSite) -> list[BrokenLink]:
    """Find all internal links that point to draft or non-existent posts.

    Builds a URL→Post map, then checks every internal link in every post.
    External links (not starting with /) are ignored.
    """
    # Build URL → Post map
    url_to_post: dict[str, Post] = {}
    for post in all_posts:
        url = site.post_url(post.metadata, post.filename)
        url_to_post[url] = post

    # Scan all posts for broken internal links
    broken: list[BrokenLink] = []
    for post in all_posts:
        links = list_links(post.content)
        for link in links:
            url = link["url"]
            # Only check internal links
            if not url.startswith("/"):
                continue

            target = url_to_post.get(url)
            if target is None:
                broken.append(BrokenLink(
                    source_post=post,
                    anchor_text=link["anchor_text"],
                    target_url=url,
                    reason="not_found",
                ))
            elif target.metadata.get("draft"):
                broken.append(BrokenLink(
                    source_post=post,
                    anchor_text=link["anchor_text"],
                    target_url=url,
                    reason="draft",
                ))

    return broken
```

Note: The `from __future__ import annotations` import must be the very first line of the file (before all other imports). This makes the type hints lazy so `Post` and `HugoSite` don't need to be imported at runtime, avoiding circular imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_linker.py::TestFindBrokenLinks -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Run all existing linker tests to ensure no regressions**

Run: `python -m pytest tests/test_linker.py -v`
Expected: All tests PASS (existing + new)

- [ ] **Step 6: Commit**

```bash
git add src/hugin/linker.py tests/test_linker.py
git commit -m "Add find_broken_links() to detect links to draft/missing posts"
```

---

### Task 2: Create BrokenLinksScreen

**Files:**
- Create: `src/hugin/tui/broken_links.py`

- [ ] **Step 1: Create the BrokenLinksScreen file**

Create `src/hugin/tui/broken_links.py`:

```python
"""Broken internal links screen — find and remove links to draft/missing posts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label, Static

from hugin.linker import (
    BrokenLink,
    find_broken_links,
    remove_specific_links,
    write_post_with_links,
)

if TYPE_CHECKING:
    from hugin.hugo import HugoSite
    from hugin.scanner import Post


class BrokenLinksScreen(ModalScreen[bool]):
    """Modal screen to find and remove broken internal links."""

    BINDINGS = [
        ("escape", "close", "Close"),
    ]

    DEFAULT_CSS = """
    BrokenLinksScreen {
        align: center middle;
    }

    #broken-links-modal {
        width: 90%;
        height: 80%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }

    #broken-links-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #broken-links-table {
        height: 1fr;
    }

    #broken-links-empty {
        width: 100%;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }

    #broken-links-buttons {
        height: auto;
        margin-top: 1;
    }

    #broken-links-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, all_posts: list[Post], site: HugoSite) -> None:
        super().__init__()
        self.all_posts = all_posts
        self.site = site
        self._broken: list[BrokenLink] = []
        self._selected: set[int] = set()
        self._changed = False

    def compose(self) -> ComposeResult:
        with Vertical(id="broken-links-modal"):
            yield Label("", id="broken-links-title")
            yield DataTable(
                id="broken-links-table",
                cursor_type="row",
                zebra_stripes=True,
            )
            yield Static(
                "No broken internal links found.",
                id="broken-links-empty",
                classes="hidden",
            )
            with Horizontal(id="broken-links-buttons"):
                yield Button(
                    "Remove selected",
                    id="btn-remove",
                    variant="error",
                )
                yield Button("Close", id="btn-close")

    def on_mount(self) -> None:
        self._broken = find_broken_links(self.all_posts, self.site)
        self._refresh()

    def _refresh(self) -> None:
        title = self.query_one("#broken-links-title", Label)
        table = self.query_one("#broken-links-table", DataTable)
        empty = self.query_one("#broken-links-empty", Static)
        btn_remove = self.query_one("#btn-remove", Button)

        table.clear(columns=True)
        self._selected.clear()

        if not self._broken:
            title.update("Broken internal links")
            table.add_class("hidden")
            empty.remove_class("hidden")
            btn_remove.add_class("hidden")
            return

        title.update(f"Broken internal links ({len(self._broken)})")
        table.remove_class("hidden")
        empty.add_class("hidden")
        btn_remove.remove_class("hidden")

        table.add_column("✓", key="selected", width=3)
        table.add_column("Source", key="source")
        table.add_column("Anchor", key="anchor")
        table.add_column("Target", key="target")
        table.add_column("Reason", key="reason")

        for i, bl in enumerate(self._broken):
            source_title = bl.source_post.metadata.get(
                "title", bl.source_post.filename,
            )
            reason_label = "draft" if bl.reason == "draft" else "not found"
            table.add_row(
                " ",
                source_title,
                bl.anchor_text,
                bl.target_url,
                reason_label,
                key=f"bl-{i}",
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_index = event.cursor_row
        if row_index is None:
            return
        table = self.query_one("#broken-links-table", DataTable)
        key = f"bl-{row_index}"
        if row_index in self._selected:
            self._selected.discard(row_index)
            table.update_cell(key, "selected", " ")
        else:
            self._selected.add(row_index)
            table.update_cell(key, "selected", "✓")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-remove":
            self._do_remove()
        elif event.button.id == "btn-close":
            self.dismiss(self._changed)

    def _do_remove(self) -> None:
        if not self._selected:
            self.notify("No links selected.", severity="warning")
            return

        # Group selected broken links by source post path
        by_post: dict[str, set[str]] = {}
        for idx in self._selected:
            bl = self._broken[idx]
            key = str(bl.source_post.path)
            if key not in by_post:
                by_post[key] = set()
            by_post[key].add(bl.target_url)

        total_removed = 0
        for idx in self._selected:
            bl = self._broken[idx]
            post_key = str(bl.source_post.path)
            if post_key not in by_post:
                continue

            urls = by_post.pop(post_key)
            post = bl.source_post
            body, removed = remove_specific_links(post.content, urls)
            write_post_with_links(post.path, body)
            post.content = body
            total_removed += removed

        self._changed = True

        # Re-scan after removal
        self._broken = find_broken_links(self.all_posts, self.site)
        self._refresh()
        self.notify(f"{total_removed} broken links removed")

    def action_close(self) -> None:
        self.dismiss(self._changed)
```

- [ ] **Step 2: Verify the file parses without errors**

Run: `python -c "from hugin.tui.broken_links import BrokenLinksScreen; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/hugin/tui/broken_links.py
git commit -m "Add BrokenLinksScreen modal for broken internal links"
```

---

### Task 3: Integrate into HuginScreen

**Files:**
- Modify: `src/hugin/tui/review.py`

- [ ] **Step 1: Add the binding**

In `src/hugin/tui/review.py`, add to the `BINDINGS` list in `HuginScreen`, after the `("l", "list_links", "List")` entry:

```python
("b", "broken_links", "Broken"),
```

- [ ] **Step 2: Add the action method**

Add this method to the `HuginScreen` class, after the `action_list_links` method (around line 1040):

```python
def action_broken_links(self) -> None:
    if self._state != STATE_BROWSING:
        return

    from hugin.tui.broken_links import BrokenLinksScreen

    def on_return(changed: bool) -> None:
        if changed:
            self._build_incoming_index()
            self._update_detail_panel()

    self.app.push_screen(
        BrokenLinksScreen(all_posts=self.all_posts, site=self.site),
        on_return,
    )
```

- [ ] **Step 3: Manually test the full flow**

1. Run: `python -m hugin /path/to/hugo/content/posts/` (use a real Hugo site directory)
2. Press `b` — the broken links modal should open
3. Verify the table shows broken links (if any exist)
4. Select rows by pressing Enter on them — the ✓ column should toggle
5. Press "Remove selected" — links should be removed, table refreshes
6. Press "Close" or Escape — modal closes, main screen updates

- [ ] **Step 4: Run all tests to ensure no regressions**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/hugin/tui/review.py
git commit -m "Add broken links screen binding (b) to main review screen"
```
