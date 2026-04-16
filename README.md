# Hugin & Munin

Two TUI tools for managing Hugo blog posts, powered by LLMs and semantic embeddings.

## Why these names?

In Norse mythology, **Hugin** (*huginn*, "thought") and **Munin** (*muninn*, "memory") are Odin's two ravens. Every day they fly over the world — Hugin gathers new knowledge, Munin remembers connections — and report back at dusk.

**Hugin** manages your blog's tags and summaries. **Munin** discovers and creates internal links between posts. Both names echo **Hugo**, the static site generator they were built for.

---

## Hugin — Tags & Summaries

### What it does

- **Tag generation** — LLM suggests tags, preferring reuse from your existing tag pool. New tags are marked with a sparkle emoji.
- **Summary generation** — LLM writes meta descriptions (140-160 chars) with personality, not generic SEO filler. Auto-retries if too long.
- **Tag management** — Audit, rename, merge (multiple sources into one target), and delete tags across your entire blog.
- **Manual tags** — Add your own tags alongside LLM suggestions.
- **Human in the loop** — Every suggestion goes through a TUI review before touching your files.

### Usage

```bash
hugin                           # Scan posts in current directory
hugin ~/blog/content/posts      # Scan a specific directory
hugin --batch 20                # Limit to 20 most recent posts
hugin --report                  # Stats only, no LLM
hugin --engine cerebras         # Use a specific engine
```

### Keybindings

| Key | Action |
|---|---|
| `t` | Generate tags with LLM |
| `s` | Generate summary with LLM |
| `m` | Open tag manager |
| `e` | Select engine and model |
| `v` | Open current post in vim (hidden from footer) |
| `Escape` | Go back / dismiss |
| `q` | Quit |

### Tag manager keybindings

| Key | Action |
|---|---|
| `m` | Merge other tags into the selected tag |
| `r` | Rename the selected tag |
| `d` | Delete the selected tag (with confirmation) |
| `q` / `Escape` | Back to main screen |

---

## Munin — Internal Links

### What it does

- **Incoming link discovery** — Finds posts that could link *to* the selected post, using semantic similarity (no LLM needed).
- **Outgoing link suggestions** — LLM identifies natural anchor text in the post body that could link to related posts. Shows context around each anchor.
- **Markdown-safe** — Never inserts links inside code blocks, headings, inline code, or existing links.
- **Link budget** — Configurable limits per post and per paragraph to avoid over-linking.
- **Atomic writes** — Files are written via temp file + rename to prevent corruption.

### How it works

1. **Embeddings** (local, no LLM) — On first run, Munin downloads a multilingual sentence-transformers model (~400 MB, one time) and builds an embedding index of all posts using title + tags + description. The index is cached in `~/.hugin/embeddings/`.

2. **Incoming (`i`)** — Pure cosine similarity search. Shows which posts are most related to the selected one. Clickable links navigate to the post in the list.

3. **Outgoing (`o`)** — Embeddings find candidate posts, then the LLM reads the full post body and candidates to find verbatim phrases that would naturally serve as anchor text. Each suggestion is validated (must exist exactly in the text, not in a protected zone, not in a saturated paragraph) before being shown.

4. **Apply** — Checked suggestions are inserted as Markdown links. The post's embedding is recomputed and the cache updated.

### Usage

```bash
munin                           # Scan posts in current directory
munin ~/blog/content/posts      # Scan a specific directory
munin --batch 50                # Limit to 50 most recent posts
munin --report                  # Show embedding cache stats
munin --engine groq             # Use a specific engine
```

### Keybindings

| Key | Action |
|---|---|
| `i` | Find incoming link candidates (embedding only, no LLM) |
| `o` | Generate outgoing link suggestions (embedding + LLM) |
| `e` | Select engine and model |
| `c` | Clear embedding cache (rebuilds on next run) |
| `Escape` | Go back |
| `q` | Quit |

### Configuration (`~/.hugin/munin.toml`)

Created automatically on first run:

```toml
[links]
max_per_post      = 8    # hard ceiling on outgoing links per post
max_per_paragraph = 1    # maximum links per paragraph
words_per_link    = 300  # 1 link per N words (capped by max_per_post)
candidates        = 10   # embedding candidates to send to LLM

[embeddings]
model = "paraphrase-multilingual-MiniLM-L12-v2"

[frontmatter]
summary_field = "description"  # field used for embeddings
```

### Hugo URL inference

Munin reads your Hugo config (`hugo.toml`, `config.toml`, or `config/_default/config.toml`) to resolve post URLs correctly. It supports:

- `permalinks` patterns with `:slug`, `:year`, `:month`, `:day`, `:section` tokens
- Frontmatter `url` field (used verbatim)
- Frontmatter `slug` field
- Filename-derived slugs (strips `YYYY-MM-DD-` date prefix)
- Multilingual content directories

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

This installs both `hugin` and `munin` commands.

For global access, create wrappers in a directory on your `$PATH`:

```bash
cat > ~/.local/bin/hugin << 'EOF'
#!/bin/sh
exec /path/to/hugin/.venv/bin/hugin "$@"
EOF

cat > ~/.local/bin/munin << 'EOF'
#!/bin/sh
exec /path/to/hugin/.venv/bin/munin "$@"
EOF

chmod +x ~/.local/bin/hugin ~/.local/bin/munin
```

---

## Shared configuration

Both tools share the config directory `~/.hugin/` and the engine configuration.

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

[groq]
url = "https://api.groq.com/openai/v1"
model = "llama-3.3-70b-versatile"
timeout = 30

[deepseek]
url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
timeout = 30

[lmstudio]
url = "http://192.168.3.36:1234/v1"
model = "google/gemma-4-26b-a4b"
timeout = 300
```

The file is created automatically on first run with sensible defaults. You can add, remove, or edit engines at any time — just edit the TOML file.

### API keys

Each engine reads its API key from an environment variable named `{ENGINE_ID}_API_KEY`:

```bash
export CEREBRAS_API_KEY="csk-..."
export OPENAI_API_KEY="sk-..."
export GROQ_API_KEY="gsk-..."
export DEEPSEEK_API_KEY="sk-..."
```

**Local engines** (localhost, 192.168.x.x, 10.x.x.x, and other private networks) don't require an API key.

### Model selection

Both tools share a dynamic engine/model picker (press `e` in either TUI):

1. Select an engine from the list
2. The tool queries the engine's `/v1/models` endpoint and lists available models
3. Pick a model — the selection persists across sessions in `~/.hugin/last_engine.json`

This is especially useful with **LM Studio** or **Ollama**, where you can load different models and switch between them without editing config files.

### Which models work best?

| Task | Recommendation | Why |
|---|---|---|
| **Tags** (Hugin) | Any instruction-following model | Simple task: read post, output JSON array |
| **Summaries** (Hugin) | Mid-size models (8B+) | Needs to write concise, natural prose |
| **Link anchors** (Munin) | Instruction models, not reasoning models | Must find verbatim text in the post body. Reasoning models (DeepSeek R1, GPT-o1) waste tokens on chain-of-thought |

**Avoid reasoning models for Munin** — they spend tokens "thinking" instead of finding text matches. Use Llama, Gemma, Qwen (with thinking disabled), or similar instruction-tuned models.

### Timeout

The `timeout` field (in seconds) controls how long to wait for LLM responses. Use higher values for local models that need time to load into memory:

```toml
[lmstudio]
timeout = 300  # 5 minutes for first load
```

### Persistence

| File | Purpose |
|---|---|
| `~/.hugin/engines.toml` | Engine definitions (shared) |
| `~/.hugin/last_engine.json` | Last selected engine+model (shared) |
| `~/.hugin/munin.toml` | Munin link settings |
| `~/.hugin/state/<hash>.json` | Hugin processing state per directory |
| `~/.hugin/embeddings/<hash>.json` | Munin embedding cache per directory |

---

## Tech stack

| Package | Role | Used by |
|---|---|---|
| [Textual](https://textual.textualize.io/) | TUI framework | Both |
| [Click](https://click.palletsprojects.com/) | CLI | Both |
| [httpx](https://www.python-httpx.org/) | Async HTTP client (LLM calls) | Both |
| [python-frontmatter](https://python-frontmatter.readthedocs.io/) | YAML frontmatter parsing | Both |
| [sentence-transformers](https://sbert.net/) | Semantic embeddings (ONNX backend) | Munin |

---

## Known limitations

Both tools are intentionally simple. The following are known limitations that we chose not to address in order to keep the codebase small and maintainable:

- **No recursive scanning** — Only reads `.md` files in the given directory. Subdirectories are ignored.
- **No page bundles** — Posts organized as `my-post/index.md` are not detected.
- **YAML frontmatter only** — Posts with TOML frontmatter (`+++`) are skipped with a warning.
- **No headless mode** — Everything goes through the TUI. There is no `--auto` flag for CI/cron pipelines.
- **No per-project config for Hugin** — Tag rules (max words, separators, article lists) are hardcoded.
- **Permalink token coverage (Munin)** — Only `:slug`, `:year`, `:month`, `:day`, `:section` are supported.

These may be addressed in future versions if there is demand.

## License

MIT
