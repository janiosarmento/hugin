# Broken Internal Links Screen

## Overview

A new ModalScreen in Hugin that scans all posts and identifies internal links pointing to draft or non-existent posts. Users can select broken links and remove them in bulk, keeping the anchor text.

## Detection Logic

### Location

New function `find_broken_links()` in `linker.py`.

### Input

- `all_posts: list[Post]` — all loaded posts
- `site: HugoSite` — for URL resolution

### Algorithm

1. Build a map `url → Post` by iterating all posts and resolving each post's URL via `site.post_url()`.
2. For each post (including drafts), extract all internal links using `list_links()` (URLs starting with `/`).
3. For each internal link URL, look it up in the map:
   - If the URL is not in the map → reason: `"not_found"`
   - If the URL maps to a post with `metadata.get("draft") == True` → reason: `"draft"`
4. Collect results as a list of `BrokenLink` dataclass instances.

### Data Structure

```python
@dataclass
class BrokenLink:
    source_post: Post
    anchor_text: str
    target_url: str
    reason: str  # "draft" or "not_found"
```

### Scope

- Only relative links (starting with `/`) are checked. External links are ignored.
- All posts are scanned as sources (including drafts — a draft linking to another draft or non-existent post is still a broken link worth knowing about).

## Screen: BrokenLinksScreen

### File

`src/hugin/tui/broken_links.py` — new file, following the pattern of `tag_manager.py`.

### Class

`BrokenLinksScreen(ModalScreen)` — returns `True` if changes were made, `False` otherwise.

### Layout

- Title at top: "Broken internal links (N)"
- `DataTable` with 3 columns:
  - **Source** — title of the post containing the broken link (fallback to filename)
  - **Target** — the broken link's target URL
  - **Reason** — "draft" or "not found"
- Row selection acts as checkbox toggle (cursor_type="row", toggle via Enter/Space)
- Bottom buttons:
  - "Remove selected" (variant `error`) — removes selected links from their source posts
  - "Close" — dismisses the modal

### Behavior

- On mount: calls `find_broken_links()`, populates the table.
- One row per broken link. A post with 3 broken links appears as 3 separate rows.
- Row selection toggles a visual indicator (e.g., checkbox column or row style change).
- "Remove selected" groups selected links by source post, then for each post:
  1. Calls `remove_specific_links(body, urls_to_remove)` once per post (avoids partial rewrites).
  2. Calls `write_post_with_links(path, body)` to persist.
  3. Updates `post.content` in memory.
- Dismisses with `True` after successful removal.

### Empty State

If no broken links are found, shows "No broken internal links found" message and only the "Close" button.

## Integration with HuginScreen

### Binding

Add `("b", "broken_links", "Broken")` to `HuginScreen.BINDINGS`.

### Action

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

### Post-removal updates

When the modal returns with `changed=True`:
- Rebuild the incoming index (`_build_incoming_index()`).
- Refresh the detail panel for the current post.
- Post content is already updated in memory by the modal (posts are shared by reference).
