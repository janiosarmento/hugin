# Hugin

A unified TUI tool for managing Hugo blog posts — tags, summaries, internal links, and editing — powered by LLMs and semantic embeddings.

## Why the name?

In Norse mythology, **Hugin** (*huginn*, "thought") is one of Odin's two ravens that flies over the world gathering knowledge. The name echoes **Hugo**, the static site generator it was built for.

---

## What it does

- **Tag generation** — LLM suggests tags, preferring reuse from your existing tag pool. New tags are marked with a sparkle emoji.
- **Summary generation** — LLM writes meta descriptions with personality, not generic SEO filler. Auto-retries if too long.
- **Internal link discovery** — Finds posts that could link to the selected post using semantic similarity (no LLM needed).
- **Outgoing link suggestions** — LLM identifies natural anchor text in the post body that could link to related posts.
- **Topic suggestions** — LLM suggests new post ideas that complement the current one, filtered by semantic similarity to avoid duplicates.
- **Built-in editor** — Edit frontmatter fields and post body directly in the TUI, with atomic saves.
- **Tag management** — Audit, rename, merge, and delete tags across your entire blog.
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
```

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
| `u` | Suggest new post topics (LLM) |
| `e` | Open built-in editor |
| `g` | Sync repository with GitHub (pull --rebase + push) |
| `n` | Select engine and model |
| `m` | Open tag manager |
| `p` | Project settings |
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
| `Escape` | Back (confirms if unsaved changes) |
| `Tab` | Navigate between fields |

---

## How it works

### Tags & Summaries

The LLM receives the post content and existing tag pool, and suggests tags and summaries. You review each suggestion with checkboxes before applying.

### Internal Links

1. **Embeddings** (local, no LLM) — On first run, Hugin downloads a multilingual sentence-transformers model (~400 MB, one time) and builds an embedding index of all posts using title + tags + description. The index is cached in `~/.hugin/embeddings/`. Draft posts are excluded from the index — if a published post is changed to draft, it is automatically removed from the cache on the next run.

2. **Incoming (`i`)** — Pure cosine similarity search. Shows which posts are most related to the selected one. Clickable links navigate to the post in the list.

3. **Outgoing (`o`)** — Embeddings find candidate posts, then the LLM reads the full post body and candidates to find verbatim phrases that would naturally serve as anchor text. Each suggestion is validated (must exist exactly in the text, not in a protected zone, not in a saturated paragraph) before being shown.

4. **Apply** — Checked suggestions are inserted as Markdown links. The post's embedding is recomputed and the cache updated.

### Editor

Press `e` to open a full-screen editor with individual input fields for frontmatter (title, date, description, etc.) and a Markdown TextArea for the body. Tags are shown read-only (use `t` for tag editing). Saves are atomic (temp file + rename).

### Git Sync

On startup, hugin automatically runs `git pull --rebase` on the posts directory so you always start with the latest content from the remote. If the pull fails due to conflicts, hugin aborts the rebase and exits with a suggested recovery command:

```
git stash && git pull --rebase && git stash pop
```

During a session, press `g` to sync manually: hugin commits any local changes, pulls with rebase, then pushes. If the pull brings new commits, the app reloads the post list and rebuilds the embedding index automatically.

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
model = "paraphrase-multilingual-MiniLM-L12-v2"

[frontmatter]
summary_field = "description"  # field used for embeddings
```

Falls back to `~/.hugin/munin.toml` if `links.toml` doesn't exist (legacy support).

### Project settings (`~/.hugin/projects/<hash>.toml`)

Per-project overrides, editable via the `p` key in the TUI:

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

### Persistence

| File | Purpose |
|---|---|
| `~/.hugin/engines.toml` | Engine definitions |
| `~/.hugin/last_engine.json` | Last selected engine + model |
| `~/.hugin/theme.json` | Textual theme selection |
| `~/.hugin/links.toml` | Global link settings |
| `~/.hugin/projects/<hash>.toml` | Per-project settings |
| `~/.hugin/state/<hash>.json` | Processing state per directory (last post, timestamps) |
| `~/.hugin/embeddings/<hash>.json` | Embedding cache per directory |

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
| **Topic suggestions** | Mid-size models (8B+) | Needs to understand the post and suggest specific angles |

**Avoid reasoning models for link anchors** — they spend tokens "thinking" instead of finding text matches. Use Llama, Gemma, Qwen (with thinking disabled), or similar instruction-tuned models.

---

## Known limitations

- **No recursive scanning** — Only reads `.md` files in the given directory.
- **No page bundles** — Posts organized as `my-post/index.md` are not detected.
- **YAML frontmatter only** — Posts with TOML frontmatter (`+++`) are skipped with a warning.
- **No headless mode** — Everything goes through the TUI.
- **Permalink token coverage** — Only `:slug`, `:year`, `:month`, `:day`, `:section` are supported.

## License

MIT
