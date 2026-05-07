# Hugin

A unified TUI tool for managing Hugo blog posts — tags, summaries, internal links, and editing — powered by LLMs and semantic embeddings.

## Why the name?

In Norse mythology, **Hugin** (*huginn*, "thought") is one of Odin's two ravens that flies over the world gathering knowledge. The name echoes **Hugo**, the static site generator it was built for.

---

## What it does

- **Tag generation** — LLM suggests tags, preferring reuse from your existing tag pool. New tags are marked with a sparkle emoji.
- **Summary generation** — LLM writes meta descriptions with personality, not generic SEO filler. Auto-retries if too long.
- **Internal link discovery** — Finds posts that could link to the selected post using semantic similarity (no LLM needed).
- **Outgoing link suggestions** — Embedding + tag matching finds candidates; LLM reranks and finds verbatim anchor text. A link profile (LLM-generated keywords) is built lazily per post to improve future searches.
- **Direct post picker** — Manually select any post and let the LLM find a natural anchor for it.
- **Amazon affiliate links** — Scans your affiliates dictionary and inserts matching links with one keystroke.
- **Topic suggestions** — LLM suggests new post ideas that complement the current one.
- **News → post ideas** — Search Google News for a topic, let the LLM generate post ideas from the headlines, and create draft `.md` files with a category, frontmatter, and a source comment block — in one keystroke.
- **Built-in editor** — Edit frontmatter fields and post body directly in the TUI, with raw mode for full-file editing. Atomic saves.
- **New post creation** — Create a blank post with minimal frontmatter, opens straight into the editor.
- **Inline search** — Press `/` to filter the post list in real-time by title or filename. `Enter`/`Escape` restores the full list.
- **Tag management** — Audit, rename, merge, and delete tags across your entire blog.
- **Git sync** — Commit local changes, pull with rebase, and push, directly from the TUI.
- **Copy to clipboard** — One-click button to copy the full `.md` file (frontmatter + body) to the clipboard.
- **Human in the loop** — Every suggestion goes through a TUI review before touching your files.

---

## Usage

```bash
hugin                           # Scan posts in current directory
hugin ~/blog/content/posts      # Scan a specific directory
hugin --batch 20                # Limit to 20 most recent posts
hugin --report                  # Stats only, no LLM
hugin --engine cerebras         # Use a specific engine
hugin --model gpt-4o            # Override model
hugin -c                        # Clear embedding cache before starting
```

### Batch link-profile generation

```bash
hugin-profiles ~/blog/content/posts             # Generate link profiles for all posts
hugin-profiles ~/blog/content/posts --force     # Regenerate even if profiles already exist
hugin-profiles ~/blog/content/posts --reset     # Delete all profiles and regenerate from scratch
hugin-profiles ~/blog/content/posts --drafts    # Include draft posts
hugin-profiles ~/blog/content/posts --engine cerebras
```

`hugin-profiles` generates LLM link profiles for every post in a directory and saves them to the keyword cache. Runs incrementally — already-profiled posts are skipped unless `--force` or `--reset` is given. `--reset` deletes the entire keyword cache before running. Interrupted runs can be safely resumed.

---

## Keybindings

| Key | Action |
|---|---|
| `t` | Generate tags with LLM |
| `s` | Generate summary with LLM |
| `i` | Find incoming link candidates (embedding only) |
| `o` | Generate outgoing link suggestions (embedding + LLM) |
| `d` | Pick a post directly and insert link |
| `z` | Insert Amazon affiliate link |
| `l` | List existing links (select to remove) |
| `b` | Check for broken links |
| `u` | Suggest new post topics (LLM) |
| `w` | News → post ideas (search Google News, generate drafts) |
| `e` | Open built-in editor |
| `p` | Create new post |
| `/` | Inline search — filter post list by title or filename |
| `g` | Sync repository with GitHub (commit + pull --rebase + push) |
| `n` | Select engine and model |
| `m` | Open tag manager |
| `,` (comma) | Project settings |
| `c` | Clear embedding cache and restart |
| `Ctrl+P` | Change Textual theme (persists across sessions) |
| `Escape` | Go back / cancel LLM call in progress |
| `q` | Quit |

### Tag manager keybindings

| Key | Action |
|---|---|
| `a` | AutoMerge — find similar tags by fuzzy match and merge them into the selected tag |
| `m` | Merge — manually pick which tags to merge into the selected tag |
| `r` | Rename the selected tag |
| `d` | Delete the selected tag (with confirmation) |
| `q` / `Escape` | Back to main screen |

### Editor keybindings

| Key | Action |
|---|---|
| `Ctrl+S` | Save (atomic write) |
| `Ctrl+R` | Toggle raw mode (edit full file as plain text) |
| `Ctrl+E` | Strip all emojis from body |
| `Ctrl+L` | Convert HTML links (`<a href="...">`) to Markdown links |
| `Escape` | Back (confirms if unsaved changes) |
| `Tab` | Navigate between fields |

---

## How it works

### Tags & Summaries

The LLM receives the post content and existing tag pool, and suggests tags and summaries. You review each suggestion with checkboxes before applying.

### Internal Links

**Embedding index** (local, no LLM) — On first run, Hugin downloads a multilingual sentence-transformers model (`intfloat/multilingual-e5-large`, ~500 MB, one time) and builds an embedding index of all posts. Each post is represented by title + description + tags + headings + body paragraphs. The index is cached in `~/.hugin/embeddings/`. Draft posts are excluded.

**Incoming (`i`)** — Pure cosine similarity search. Shows which posts are most related to the selected one. Clickable links navigate to the post in the list.

**Outgoing (`o`)** — Multi-stage pipeline:

1. **Link profile** (lazy, one LLM call per post, cached) — On the first run for a post, the LLM extracts 10–15 focused keywords describing the post's topics. The profile is visible in the detail panel and used to improve similarity matching. Profiles are stored in a separate `_kw.json` file that survives embedding cache clears. Pressing `o` always regenerates the profile for the current post.

2. **Candidate search** — Two pools are merged: semantic similarity (top 4× candidates) and tag-based candidates (posts sharing ≥2 tags, ranked by IDF-weighted overlap). Pools are deduplicated and ranked by score, which includes cosine similarity + mention boost (slug keywords found in body) + tag IDF boost.

3. **LLM reranking** — The LLM filters the merged pool to genuinely related posts. It runs in inclusive mode — borderline candidates are kept for human review. A minimum of 3 candidates always passes through regardless of the LLM response.

4. **LLM anchor finding** — The LLM identifies verbatim phrases in the post body that would naturally serve as anchor text for each candidate. Each anchor is validated: must exist exactly in the text, not inside a heading/code/existing link, not in a paragraph already at its link limit.

5. **Keyword fallback** — Candidates the LLM missed get a deterministic search derived from the target post's URL slug. Hugin tries the full slug phrase first ("leucemia felina"), then progressively shorter sub-phrases, then individual words (≥6 chars). Matching is accent-insensitive so slugs without diacritics still find accented body text. The longest matching phrase is used as anchor.

**Apply** — Checked suggestions are inserted as Markdown links. The post's embedding is recomputed and the cache updated.

### News → Post Ideas (`w`)

Press `w` to open the news ideas screen:

1. Enter a search term (e.g. `cats`, `kubernetes`, `finanças`).
2. Hugin fetches the top 25 headlines from Google News RSS (language and region follow the query — no geo-restriction).
3. The LLM generates up to 8 post ideas inspired by the headlines. Each idea has a title, a 1–2 sentence description, and a category automatically matched to your blog's existing categories (read from `.pages.yml` if present).
4. Review the ideas with checkboxes. Press `Ctrl+S` or "Create drafts" to write the selected posts as `.md` files.

Each draft is created with:
- Frontmatter: `title`, `date`, `categories`, `draft: true`
- A comment block in the body with the idea description, the search query, and the related headlines

### Inline Search (`/`)

Press `/` from the main screen to activate the post filter. Start typing — the list updates in real-time, matching against title and filename. Press `Enter` or `Escape` to restore the full list with the cursor on the last selected post.

### Editor

Press `e` to open a full-screen editor. Frontmatter fields (title, date, slug, description, etc.) are individual inputs; the body is a Markdown TextArea limited to 80 columns.

Press `Ctrl+R` to toggle **raw mode**: the entire file is shown as plain text in a single TextArea. Useful for pasting LLM-generated frontmatter. When switching back to structured mode, `title` and `description` values are automatically quoted to prevent YAML parse errors. Saves are atomic (temp file + rename).

### Git Sync

On startup, Hugin:
1. Commits any local changes (`git add -A && git commit`)
2. Pushes to remote — exits with a fix suggestion if push fails
3. Pulls with rebase — exits with a stash suggestion if there are conflicts

During a session, press `g` to sync manually. The same flow runs: commit → pull --rebase → push. If the pull brings new commits from remote, the app reloads the post list and rebuilds the embedding index automatically.

---

## Configuration

### Engines (`~/.hugin/engines.toml`)

Any OpenAI-compatible endpoint works. Each section defines an engine:

```toml
[cerebras]
url = "https://api.cerebras.ai/v1"
model = "llama-4-scout-17b-16e-instruct"
timeout = 30

[openai]
url = "https://api.openai.com/v1"
model = "gpt-4o"
timeout = 30

[lmstudio]
url = "http://192.168.3.36:1234/v1"
model = "google/gemma-4-26b-a4b"
timeout = 300
```

Created automatically on first run. API keys use environment variables: `{ENGINE_ID}_API_KEY` (e.g., `CEREBRAS_API_KEY`). Local engines don't require a key.

### Link settings (`~/.hugin/links.toml`)

Global defaults for link management:

```toml
[links]
max_per_post      = 8    # hard ceiling on outgoing links per post
max_per_paragraph = 1    # maximum links per paragraph
words_per_link    = 300  # 1 link per N words (capped by max_per_post)
candidates        = 10   # embedding candidates to send to LLM
max_anchor_words  = 5    # max words in an anchor phrase

[embeddings]
model = "intfloat/multilingual-e5-large"

[frontmatter]
summary_field = "description"  # frontmatter field used in embeddings
```

### Affiliate links (`~/.hugin/affiliates.toml`)

Maps keywords to affiliate URLs. Press `z` in the main screen to scan the current post and insert matching links automatically.

```toml
"caixa de areia" = "https://amzn.to/abc123"
"arranhador" = "https://amzn.to/def456"
```

### Project settings (`~/.hugin/projects/<hash>.toml`)

Per-project overrides, editable via the `,` key in the TUI:

```toml
[summary]
words = 25
style = "Write as if telling a friend — direct, with personality"

[links]
words_per_link = 0  # 0 = use global default
```

- **Summary words** — Target word count for generated summaries (5-50).
- **Summary style** — Tone instruction sent to the LLM. Free text.
- **Words per link** — Override link density per project. Lower = more links. 0 = use global default from `links.toml`.

### Hugo URL inference

Hugin reads your Hugo config (`hugo.toml`, `config.toml`, or `config/_default/`) to resolve post URLs correctly. It supports:

- `permalinks` patterns with `:slug`, `:year`, `:month`, `:day`, `:section` tokens
- Frontmatter `url` field (used verbatim)
- Frontmatter `slug` field
- Filename-derived slugs (strips `YYYY-MM-DD-` date prefix)
- Multilingual content directories

### AI agent file handling

If your content directory contains `CLAUDE.md` or `AGENTS.md` (instruction files for AI coding agents), Hugin automatically:

- **Excludes them from the post list** — they never appear in the TUI or get processed as posts.
- **Adds them to Hugo's `ignoreFiles`** — on first run, Hugin finds your `hugo.toml` (or `config.yaml`) and appends anchored regex patterns (`^CLAUDE\.md$`, `^AGENTS\.md$`) to the `ignoreFiles` list so Hugo doesn't include them in the build. This is idempotent — repeated runs don't duplicate entries.

### Persistence

| File | Purpose |
|---|---|
| `~/.hugin/engines.toml` | Engine definitions |
| `~/.hugin/last_engine.json` | Last selected engine + model |
| `~/.hugin/theme.json` | Textual theme selection |
| `~/.hugin/links.toml` | Global link settings |
| `~/.hugin/affiliates.toml` | Affiliate link dictionary |
| `~/.hugin/projects/<hash>.toml` | Per-project settings |
| `~/.hugin/state/<hash>.json` | Processing state per directory (last post, timestamps) |
| `~/.hugin/embeddings/<hash>.json` | Embedding cache per directory |
| `~/.hugin/embeddings/<hash>_kw.json` | Link-profile keywords per post (survives cache clears) |

---

## Installation

Requires Python 3.11+.

```bash
git clone https://github.com/janiosarmento/hugin.git
cd hugin
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs the `hugin` command.

For global access, create a wrapper in a directory on your `$PATH`:

```bash
cat > ~/.local/bin/hugin << 'EOF'
#!/bin/sh
exec /path/to/hugin/.venv/bin/hugin "$@"
EOF

chmod +x ~/.local/bin/hugin
```

---

## Which models work best?

| Task | Recommendation | Why |
|---|---|---|
| **Tags** | Any instruction-following model | Simple task: read post, output JSON array |
| **Summaries** | Mid-size models (8B+) | Needs to write concise, natural prose |
| **Link anchors** | Instruction models, not reasoning | Must find verbatim text in the post body |
| **Reranking / link profile** | 32B+ models preferred | Better judgment on topical relevance |
| **Topic suggestions** | Mid-size models (8B+) | Needs to understand the post and suggest specific angles |

**Avoid reasoning models for link anchors** — they spend tokens "thinking" instead of finding text matches. Use Llama, Gemma, Qwen (with thinking disabled), or similar instruction-tuned models.

**For reranking and link profiles**, larger models (32B+) produce significantly better results. Local models via LM Studio work well on Apple Silicon with MLX.

---

## Known limitations

- **No recursive scanning** — Only reads `.md` files in the given directory.
- **No page bundles** — Posts organized as `my-post/index.md` are not detected.
- **YAML frontmatter only** — Posts with TOML frontmatter (`+++`) are skipped with a warning.
- **No headless mode** — Everything goes through the TUI.
- **Permalink token coverage** — Only `:slug`, `:year`, `:month`, `:day`, `:section` are supported.

## License

MIT
