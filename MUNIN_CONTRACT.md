# Munin — Project Contract (Final)

## Overview

Munin is a TUI tool for managing internal links in Hugo blog posts. It is a companion to Hugin (tag and summary management) and lives in the same repository, sharing infrastructure where possible.

The name follows the same Norse mythology convention: Munin (*memory*, Old Norse *Muninn*) is Odin's second raven. Where Hugin flies to gather new knowledge (tags, summaries), Munin remembers the connections between things.

---

## Repository integration

Munin is added to the existing Hugin repository. No new repository is created.

### Package structure

**Read Hugin's existing source layout before writing any code.** Munin must not duplicate code that Hugin already provides — specifically the LLM client (httpx-based, OpenAI-compatible), the frontmatter parser, and the engine/model selector. Import those modules directly from the `hugin` package.

**Shared infrastructure rule:** Munin must reuse existing Hugin infrastructure wherever a suitable component already exists. This includes shared configuration loading, status/error reporting, common Textual widgets, and UI conventions already present in Hugin. Claude Code must not introduce parallel implementations of an existing Hugin concept.

If a small shared abstraction is missing and duplication would otherwise be required, it may be extracted into the `hugin` package only when strictly necessary and only as a minimal targeted refactor. No broad cleanup or unrelated refactoring is allowed.

### Additions to the repo

```
src/
└── munin/
    ├── __init__.py
    ├── __main__.py
    ├── cli.py           # Click entry point
    ├── tui.py           # Textual application
    ├── embeddings.py    # sentence-transformers index
    ├── hugo.py          # Hugo config parsing and URL inference
    ├── linker.py        # Markdown-safe anchor detection and substitution
    └── state.py         # Per-session state
```

### pyproject.toml

Add a second entry point to the existing `pyproject.toml`:

```toml
[project.scripts]
hugin = "hugin.cli:main"
munin = "munin.cli:main"
```

Add new dependencies (do not remove existing ones):

```toml
"sentence-transformers[onnx]>=3.0",
```

The ONNX backend avoids the ~2GB torch dependency. Use `backend="onnx"` when loading the model.

---

## Data directory

All Munin data lives under `~/.hugin/` alongside Hugin's existing files:

```
~/.hugin/
├── engines.toml          # shared with Hugin (read-only from Munin's perspective)
├── last_engine.json      # shared with Hugin (Munin reads and writes this)
├── state/                # Hugin state (untouched by Munin)
├── munin.toml            # Munin configuration (created on first run)
└── embeddings/
    └── <hash>.json       # one file per blog directory
```

The `<hash>` is the MD5 of the resolved absolute path of the scanned posts directory.

---

## Configuration (`~/.hugin/munin.toml`)

Created automatically on first run with these defaults:

```toml
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
```

The computed link budget for a post is:

```
budget = min(max_per_post, floor(word_count / words_per_link))
```

Both `max_per_post` and `max_per_paragraph` are enforced as hard limits regardless of how many candidates the LLM finds.

A paragraph is defined as a block of text separated by blank lines (`\n\n`). Lists and blockquotes form their own blocks and are not eligible for link insertion.

---

## Hugo URL inference (`munin/hugo.py`)

Munin needs the relative URL of every post to generate correct Markdown links. It must not hardcode a URL pattern.

### Discovery of `hugo.toml`

Walk up from the scanned posts directory until a file named `hugo.toml`, `config.toml`, or `config.yaml` is found, or the filesystem root is reached. If no config file is found, fall back to filename-based URLs and emit a warning in the status bar.

### Section inference

The section is the first directory component under `content/` in the Hugo site tree. If the section cannot be determined from the directory structure, fall back to the name of the scanned directory and emit a warning.

### URL resolution order (per post)

1. If frontmatter contains a `url` field, use it verbatim.
2. If frontmatter contains a `slug` field, use it as the slug component.
3. Otherwise, derive the slug from the filename: strip the `.md` extension and, if the filename starts with a date prefix (`YYYY-MM-DD-`), strip that prefix too.

Apply the `permalinks` pattern from the Hugo config for the matching content section. If no `permalinks` entry matches the section, default to `/:section/:slug/`.

Supported permalink tokens: `:slug`, `:year`, `:month`, `:day`, `:section`. Derive year/month/day from the frontmatter `date` field. If `date` is absent, fall back to the file's modification time. If an unsupported token is encountered in the configured pattern, emit a warning and fall back to `/:section/:slug/`.

The result is always a root-relative path (begins with `/`, ends with `/`).

---

## Embedding subsystem (`munin/embeddings.py`)

### Model

`paraphrase-multilingual-MiniLM-L12-v2` from `sentence-transformers`. Covers Portuguese and English in a shared vector space.

### First-run download

On the first run, `sentence-transformers` downloads the model. Print progress messages to the terminal before the TUI opens:

```
Loading embedding model (first run may download ~400 MB)...
Building embeddings for 398 posts...
  [148/398] processing post-name.md...
Done. Opening TUI.
```

Show incremental progress (post count) rather than a progress bar. Do not suppress download output.

### What to embed per post

Concatenate, in order:

1. The `title` frontmatter field
2. The `tags` frontmatter field (joined as a space-separated string)
3. The value of the configured `summary_field` (default: `description`)

If `summary_field` is absent from a post's frontmatter, embed title + tags only. Do not embed the post body.

### Cache format (`~/.hugin/embeddings/<hash>.json`)

```json
{
  "version": 1,
  "posts": {
    "/absolute/path/to/post.md": {
      "mtime": 1713300000.0,
      "url": "/posts/my-post/",
      "title": "My Post",
      "embedding": [0.123, -0.456, "..."]
    }
  }
}
```

### Cache invalidation

On startup, load the cache file for the current blog directory. For each `.md` file in the directory:

- If the file is not in the cache, generate its embedding and add it.
- If the file's `mtime` has changed since the cached value, regenerate its embedding.
- If a cached entry no longer has a corresponding file, remove it.

Generate embeddings in a single batch call to `model.encode()`, not one at a time.

### Post-write cache update

After modifying a post file (applying outgoing links), Munin must recompute the embedding for that post and update its cache entry with the new `mtime` and embedding vector.

### Similarity search

Use cosine similarity. For a given query post, return the top N most similar posts (configured as `candidates`), excluding:

- The query post itself (compare by resolved absolute path)
- Posts that the query post already links to (see Existing-link detection below)

---

## Existing-link detection

When determining which posts the current post already links to, scan the post body for all of the following:

- Inline Markdown links: `[text](url)`
- Reference-style Markdown links: `[text][ref]` with the corresponding `[ref]: url` definition
- Autolinks: `<https://...>`
- HTML anchor tags: `<a href="...">...</a>`

Images (`![alt](url)`) do not count as links for this purpose.

Extract the URL from each detected link and resolve it against the blog's base URL to determine whether it refers to another post in the same blog. Exclude any matched posts from the candidate list.

---

## TUI layout (`munin/tui.py`)

Built with Textual. Follow Hugin's visual conventions exactly (colour scheme, widget sizing, status bar style).

```
┌─────────────────────────────────────────────────────────────┐
│  Munin — /path/to/blog/content/posts                        │
├───────────────────────┬─────────────────────────────────────┤
│  Posts                │  Possible incoming links            │
│  ─────────────────    │  ──────────────────────────────     │
│  ▶ My post title      │  • Post that might link here        │
│    Another post       │  • Another potential linker         │
│    Yet another one    │                                     │
│                       │  Outgoing link suggestions          │
│                       │  ──────────────────────────────     │
│                       │  ☑  "systemd timers" → /posts/st…  │
│                       │  ☑  "cron jobs"      → /posts/cr…  │
│                       │  ☐  "launchd"        → /posts/la…  │
│                       │                                     │
├───────────────────────┴─────────────────────────────────────┤
│  [i] incoming  [o] outgoing  [e] engine  [a] apply  [q] quit│
└─────────────────────────────────────────────────────────────┘
```

### Left column: post list

A `DataTable` with columns: status indicator, title, date. Identical in behaviour to Hugin's post list. Sorted by publication date, newest first. Only `.md` files with YAML frontmatter are shown; TOML frontmatter files (`+++`) are skipped with a warning in the status bar.

### Right column: two stacked sections

**Possible incoming links** (top half): a read-only list. Each item is the title of a post that the embedding search identified as a likely candidate for linking *to* the current post. No actions are available on this list. It is informational only: "these posts might benefit from linking here; open them in Munin to add outgoing links."

**Outgoing link suggestions** (bottom half): a list of checkboxes. Each item shows the proposed anchor text (truncated if necessary), an arrow, and the target post's URL. All items are checked by default. The user unchecks items to veto them.

### Keybindings

| Key | Action |
|-----|--------|
| `i` | Run incoming analysis for selected post (embedding only; no LLM) |
| `o` | Run outgoing analysis for selected post (embedding + LLM) |
| `s` | Suggest new post topics based on content gaps (LLM + embedding filter) |
| `l` | List all links (internal and external) in the selected post for selective removal |
| `e` | Open engine/model selector (reuse Hugin's widget) |
| `c` | Clear embedding caches and restart the application |
| `a` | Context-dependent: "Insert links" in outgoing mode, "Remove links" in list mode |
| `Space` | Toggle checkbox in outgoing suggestions or link list |
| `Delete` / `Backspace` | Strip all internal links from the selected post (hidden, with confirmation) |
| `q` | Quit |
| `Escape` | Return to browsing / dismiss overlay |

### Panel clearing

Pressing `i`, `o`, or `l` always clears the entire detail panel (incoming, outgoing, suggest, and buttons) before doing anything else. This prevents stale results from a previous mode bleeding into the new one.

### Apply button label

The Apply button label changes based on the active review mode:
- **Outgoing mode (`o`):** "Insert links"
- **List mode (`l`):** "Remove links"

### Apply with no suggestions

Pressing `a` when there is nothing to do must be a no-op and display an appropriate message in the status bar:

| Situation | Status bar message |
|-----------|-------------------|
| `o` has not been run yet | `Press 'o' first to generate outgoing suggestions.` |
| `o` ran but found nothing | `No suggestions for this post.` |
| All checkboxes are unchecked | `No links selected.` |

### State persistence

Incoming and outgoing results (including checkbox state) are kept in memory for the session. They are not persisted to disk — each session starts fresh.

---

## Incoming links workflow

Triggered by pressing `i` on a selected post.

1. Run cosine similarity search against the embedding cache.
2. Return up to `candidates` results.
3. Populate the incoming list in the right column with the titles of those posts.
4. No LLM call. No file is touched.

---

## Outgoing links workflow

Triggered by pressing `o` on a selected post.

### Step 1 — Budget check

Compute the link budget before doing anything else:

```
budget = min(max_per_post, floor(word_count / words_per_link))
```

If `budget == 0`, skip the LLM call entirely and display in the status bar:

```
Post too short for link suggestions under current policy.
```

### Step 2 — Candidate discovery (embedding)

Run cosine similarity search. Return up to `candidates` results. These are the candidate target posts.

### Step 3 — Anchor detection (LLM)

Send a single LLM request with the following structure.

**System prompt:**

```
You are a technical blog editor. Your task is to identify natural anchor text within a blog post body that could serve as an internal link to related posts.

Rules:
- The anchor_text must appear verbatim in the post body.
- Prefer specific technical terms, tool names, or concepts over generic phrases.
- Do not suggest anchors inside headings, code blocks, inline code, images, or existing links.
- Suggest at most one anchor per candidate post.
- Omit candidates for which no natural anchor exists — do not force one.
- Return a JSON array and nothing else. No preamble, no markdown fences.
```

**User message:**

```
Post body:
<full body text of the current post>

Candidate posts (suggest an anchor for each where natural):
[{"title": "...", "summary": "...", "url": "/posts/foo/"}]

Return format:
[{"target_url": "/posts/foo/", "anchor_text": "exact phrase from body"}]
```

**Response schema:**

```json
[
  {
    "target_url": "/posts/systemd-timers/",
    "anchor_text": "systemd timers"
  }
]
```

The LLM may return an empty array if no natural anchors exist.

### Step 4 — Validation and retry

For each returned item:

1. **Anchor length check:** if the anchor has more words than `max_anchor_words` (default 5), discard it immediately. This prevents the LLM from linking entire sentences or paragraphs.
2. **Verbatim check:** validate that `anchor_text` appears verbatim in the post body (case-sensitive). If it does not, retry **per item** with a corrective prompt that includes the full post body, the specific candidate, and the invalid anchor:

```
The phrase '{anchor_text}' does not appear verbatim in the post body.
Choose a phrase from the body that exists exactly as written and would
naturally link to: {title} ({url})
```

If the retry also fails (or the retried anchor exceeds `max_anchor_words`), discard the suggestion silently and log a warning to the status bar. Do not retry more than once per item.

Additionally verify that the anchor text does not fall inside a protected zone (see Markdown-safe substitution below). If it does, discard silently.

### Step 5 — Budget enforcement

If more suggestions were returned than the budget allows, keep only the top `budget` items in the order returned by the LLM (the LLM is implicitly ranking by relevance).

Populate the outgoing suggestions section with the validated, budget-capped list.

---

## Applying links (`munin/linker.py`)

Triggered by pressing `a`. Only checked items are applied.

### Protected zones

Use a Markdown-aware parser (not regex alone) to identify all ranges in the raw source that must not be modified:

- Fenced code blocks (` ``` `)
- Inline code spans (`` ` ``)
- ATX and setext headings
- Existing Markdown links (`[text](url)`, `[text][ref]`)
- Autolinks (`<https://...>`)
- Images (`![alt](url)`)
- HTML anchor tags (`<a href="...">...</a>`)

No substitution may occur within any protected zone.

### HTML `<a>` conversion

When Munin encounters a raw HTML anchor tag `<a href="/posts/foo/">anchor text</a>` in the post body that links to another post in the same blog, it should convert it to Markdown format `[anchor text](/posts/foo/)` as part of the apply step, provided the conversion is safe (i.e. the tag spans a single line and contains only plain text — no nested HTML). This conversion is mandatory when the conditions are met, not optional.

### Substitution logic

Process checked suggestions in the order they appear in the list. For each suggestion:

1. Search for the first **whole-word** occurrence of `anchor_text` in the post body that:
   - Respects word boundaries (the match must not start or end mid-word; e.g. "programação subliminar" must not match inside "re**programação subliminar**")
   - Is not inside a protected zone
   - Is not inside a paragraph that already contains a link (enforcing `max_per_paragraph`)
   - Has not been consumed by a previously applied substitution in this session
2. If a valid occurrence is found, replace it with `[anchor_text](target_url)`.
3. If no valid occurrence is found, skip this suggestion and note it in the status bar.

The first-occurrence-wins rule applies both across the raw text and within a single apply operation: if two suggestions would consume overlapping text, the one processed first wins and the other is skipped.

### Frontmatter preservation

Use Hugin's `python-frontmatter` approach with `sort_keys=False` to preserve field order. Update `lastmod` with the current timestamp on every save, same as Hugin does.

Always use the system timezone. Do not hardcode any timezone offset. If the system timezone cannot be determined, fall back to UTC.

### Write strategy

1. Read the file.
2. Apply all checked substitutions to the body.
3. Update `lastmod` in the frontmatter in memory.
4. Write the entire file back atomically: write to a temporary file in the same directory, then `os.replace()`.
5. Recompute the embedding for this post and update the cache entry.

---

## CLI interface (`munin/cli.py`)

```
munin [OPTIONS] [DIRECTORY]
```

| Option | Description |
|--------|-------------|
| `DIRECTORY` | Path to the posts directory. Defaults to current directory. |
| `--batch N` | Limit the post list to the N most recent posts. |
| `--report` | Print embedding cache stats and exit. No TUI, no LLM. |
| `--engine NAME` | Override the engine (same names as `engines.toml`). |
| `-h, --help` | Show help and exit. |

No `--auto` flag. Munin is always interactive.

---

## Self-link prevention

When building the candidate list for any post, always exclude that post itself. Compare by resolved absolute path, not by URL or title.

---

## Error handling

Any LLM call failure (network error, malformed JSON response, timeout) must surface in the Textual status bar without crashing the application. The status bar message should be concise and actionable (e.g. `LLM error: connection timeout. Press 'o' to retry.`).

---

## List links workflow

Triggered by pressing `l` on a selected post.

1. Scan the post body for **all** Markdown links — both internal (`/path/...`) and external (`https://...`).
2. Display each link with its anchor text, surrounding context, and target URL.
3. Each link has a checkbox (unchecked by default). The user checks links to mark them for removal.
4. Pressing `a` (Apply) removes checked links by replacing `[anchor](url)` with just `anchor`, keeping the text intact.

This is the same Apply button used by the outgoing workflow; the `_review_mode` flag (`"list"` vs `"outgoing"`) determines which action is taken.

---

## Suggest workflow

Triggered by pressing `s` on a selected post.

1. Send the post's title, tags, and description to the LLM with a prompt asking for new post topic suggestions that would complement the existing content.
2. For each suggested topic, compute its embedding and check cosine similarity against all existing posts.
3. Topics that are too similar to an existing post (similarity ≥ 0.75) are filtered out — the blog likely already covers them.
4. Display surviving suggestions with a clipboard-copy action (OSC 52).

No files are modified. This is an informational feature for content planning.

---

## Strip all internal links

Triggered by pressing `Delete` or `Backspace` on a selected post. This is a hidden (unlabeled) destructive action.

1. A confirmation screen appears with focus on "No" to prevent accidental use.
2. If confirmed, all internal links (URLs starting with `/`) are removed from the post body, replacing `[text](/url)` with `text`.
3. The file is saved atomically. The embedding cache is updated.

External links are not affected.

---

## Clear caches

Triggered by pressing `c`.

1. A confirmation screen appears.
2. If confirmed, the embedding cache file for the current blog directory is deleted.
3. The application exits with code 42, which the CLI interprets as a restart signal. The CLI re-launches the TUI, which rebuilds all embeddings from scratch.

---

## No-outgoing cache

When the outgoing workflow (`o`) runs and finds no viable link suggestions for a post, that post is marked in the embedding cache with a `no_outgoing` flag. On subsequent sessions:

- Posts with this flag display a visual indicator ("no opportunities") in their metadata panel.
- The flag is cleared automatically when the post's `mtime` changes (i.e. the post is edited).

This prevents wasting time re-analysing posts that have already been evaluated with no results.

---

## Known limitations (document in README)

The following are intentional limitations, not bugs:

- **No recursive scanning.** Only `.md` files in the given directory. Subdirectories and page bundles (`my-post/index.md`) are ignored.
- **YAML frontmatter only.** Posts with TOML frontmatter (`+++`) are skipped with a warning.
- **Configurable summary field.** Munin reads the field named in `summary_field` (default: `description`) for embeddings. Blogs that use `summary` or a custom field must update `munin.toml`.
- **Permalink token coverage.** Only `:slug`, `:year`, `:month`, `:day`, `:section` are supported. If an unsupported token is encountered, Munin warns and falls back to `/:section/:slug/`.
- **No headless mode.** No `--auto` flag for CI pipelines.
- **No batch warming.** Posts are analysed on demand. A future warming mode would pre-analyse the entire blog and cache suggestions to disk for bulk approval.

---

## Implementation notes for Claude Code

1. **Read Hugin's source first.** Identify the exact module paths for the LLM client, frontmatter utilities, and engine selector widget before writing any Munin code. Import them; do not reimplement them.
2. **Do not modify any existing Hugin file** except `pyproject.toml` (to add the `munin` entry point and new dependencies). The only permitted exception is a minimal targeted extraction into the `hugin` package if shared code is strictly required and does not exist yet.
3. **Test the URL inference** against at least three cases: `url` in frontmatter, `slug` in frontmatter, and filename-derived slug with a date prefix.
4. **Test the Markdown-safe substitution** against posts that contain the anchor phrase inside fenced code blocks, inline code spans, and HTML `<a>` tags — none of these must be touched.
5. **Generate embeddings in a single batch**, not in a per-file loop.
6. **Never crash on LLM failure.** Every call to the LLM must be wrapped in error handling that surfaces the problem in the status bar and leaves the application in a usable state.