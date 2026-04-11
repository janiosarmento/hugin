# Hugin

A TUI tool for managing Hugo blog post tags and summaries, powered by any OpenAI-compatible LLM.

## Why "Hugin"?

In Norse mythology, **Hugin** (from Old Norse *huginn*, "thought") is one of Odin's two ravens. Every day, Hugin flies over the world gathering knowledge and reports back to Odin at dusk.

Hugin does the same for your blog: it scans your posts, consults an LLM to generate tags and summaries, and reports back for your review. You remain in control — Hugin thinks, you decide.

The name also echoes **Hugo**, the static site generator it was built for.

## What it does

- **Tag generation** — LLM suggests tags for each post, preferring reuse from your existing tag pool
- **Summary generation** — LLM writes meta descriptions (140-160 chars) with personality, not generic SEO filler
- **Tag management** — Audit, rename, merge, and delete tags across your entire blog in one screen
- **Manual tags** — Add your own tags alongside LLM suggestions
- **Human in the loop** — Every suggestion goes through a TUI review before touching your files
- **Multi-engine** — Works with any OpenAI-compatible endpoint: OpenAI, Cerebras, Groq, DeepSeek, LM Studio, Ollama, and more
- **Dynamic model selection** — Pick engine and model from the TUI, with automatic `/v1/models` discovery

## Install

Requires Python 3.11+.

```bash
git clone https://github.com/your-user/hugin.git
cd hugin
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

For global access, create a wrapper:

```bash
cat > ~/.local/bin/hugin << 'EOF'
#!/bin/sh
exec /path/to/hugin/.venv/bin/hugin "$@"
EOF
chmod +x ~/.local/bin/hugin
```

## Usage

```bash
# Scan posts in current directory
hugin

# Scan a specific directory
hugin ~/blog/content/posts

# Limit to 20 posts
hugin --batch 20

# Report mode (no LLM, just stats)
hugin --report

# Use a specific engine
hugin --engine cerebras
```

## TUI keybindings

| Key | Action |
|---|---|
| `t` | Generate tags with LLM |
| `s` | Generate summary with LLM |
| `m` | Open tag manager (audit, rename, merge, delete) |
| `e` | Select engine and model |
| `Escape` | Go back / dismiss |
| `q` | Quit |

## Configuration

All config lives in `~/.hugin/`.

### Engines (`~/.hugin/engines.toml`)

Created automatically on first run. Each section is an engine:

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

### API keys

Set as environment variables: `{ENGINE_ID}_API_KEY`.

```bash
export CEREBRAS_API_KEY="..."
export OPENAI_API_KEY="sk-..."
```

Engines on private networks (localhost, 192.168.x.x, 10.x.x.x) don't require an API key.

### Persistence

- `~/.hugin/last_engine.json` — Last selected engine+model (restored on next run)
- `~/.hugin/state/<hash>.json` — Processing state per blog directory

## How it works

1. Reads all `.md` files from the target directory (YAML frontmatter only; TOML frontmatter posts are skipped with a warning)
2. Sorts by publication date (newest first)
3. Opens a TUI with a DataTable of posts
4. On `t` or `s`, calls the LLM **on-demand** — no tokens wasted on posts you don't review
5. Shows suggestions with checkboxes for approval/veto
6. Writes approved changes to the frontmatter, preserving field order

### Tag pool

Hugin collects all existing tags from the blog and sends them to the LLM sorted by frequency. The LLM is instructed to strongly prefer reusing existing tags, keeping your taxonomy consistent. New tags are marked with a sparkle emoji in the TUI.

### Normalization

Tags from the LLM go through a safety net: lowercase, hyphens, article removal, 3-word max. Accents and Unicode are preserved — URL cleanup is Hugo's responsibility (`removePathAccents`).

### Summary constraints

Summaries are strictly capped at 160 characters. If the LLM overshoots, Hugin automatically retries with a shortening prompt (up to 2 attempts).

## Tech stack

| Package | Role |
|---|---|
| [Textual](https://textual.textualize.io/) | TUI framework |
| [Click](https://click.palletsprojects.com/) | CLI |
| [httpx](https://www.python-httpx.org/) | Async HTTP client |
| [python-frontmatter](https://python-frontmatter.readthedocs.io/) | YAML frontmatter parsing |

## Known limitations

Hugin is intentionally simple. The following are known limitations that we chose not to address in order to keep the codebase small and maintainable:

- **No recursive scanning** — Only reads `.md` files in the given directory. Subdirectories are ignored.
- **No page bundles** — Posts organized as `my-post/index.md` are not detected.
- **YAML frontmatter only** — Posts with TOML frontmatter (`+++`) are skipped with a warning.
- **No headless mode** — Everything goes through the TUI. There is no `--auto` flag for CI/cron pipelines.
- **No per-project config** — Tag rules (max words, separators, article lists) are hardcoded. A future `hugin.yaml` could make them configurable.

These may be addressed in future versions if there is demand.

## License

MIT
