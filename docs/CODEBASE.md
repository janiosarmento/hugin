# Hugin codebase map

A map for reading the code: what each module is for, what happens at startup,
where each feature lives, and the rules the code relies on. Keep it current
when a module or a rule changes.

## Startup (`hugin/cli.py`, `main`)

1. Ignore SIGTSTP (Ctrl+Z must not stop Hugin outside the TUI).
2. Load `~/.hugin/engines.toml`, `links.toml` and the rest of the global config.
3. Git sync (`git.git_sync`): commit everything, `pull --rebase`, push. Exits on failure.
4. Load the posts (`scanner.load_posts`), from the folder given on the command line.
5. Migrations (`migrations.run_migrations`). If any changed files, sync again and reload the posts.
6. Hugo site (`hugo.HugoSite`) and the agent-file ignore for Hugo builds.
7. Embedding index (`embeddings.EmbeddingIndex.build`). Runs before the TUI, so it sees the migrated posts.
8. `--report` prints stats and stops. Otherwise the TUI runs. Return code 42 restarts it (cache cleared).

## Modules

| Module | Responsibility |
| --- | --- |
| `cli.py` | Entry point, startup order above, `--report`, `hugin-profiles` command |
| `migrations.py` | Four independent, idempotent startup steps (see below) |
| `project.py` | Per-site settings: `hugin/config.toml` and the author list in `data/authors.toml` |
| `authors.py` | Author helpers: post author, author list, default, import, default for posts |
| `hugo.py` | Hugo config lookup, site root (`find_site_root`), post URL inference |
| `scanner.py` | Reads posts: front matter, dates, drafts, tags |
| `writer.py` | Single path for writing front matter; `create_post` |
| `echo.py` | Echo client and the sample/prompt logic for `h` and `R` (no UI) |
| `llm.py`, `engines.py` | System LLM calls and the engine registry |
| `embeddings.py` | Sentence embeddings, cache, similar-post ranking |
| `linker.py` | Internal and affiliate link insertion; anchors that do not break Markdown |
| `normalizer.py` | Cleans tags coming from the LLM |
| `news.py` | Fetches news for the post-idea flow |
| `redirects.py` | `_redirects` file (Netlify / Cloudflare Pages) |
| `git.py` | Git sync for the whole repo, and `commit_settings` for the settings files only |
| `state.py` | Processing state (which posts were tagged, when) |
| `affiliates.py`, `config.py` | Affiliate keyword map; links configuration |
| `trash.py` | Deleted posts go to a trash folder, never removed for good |
| `fsutil.py` | `atomic_write_text`, used by everything that persists data |
| `log.py` | Error log; the UI shows one short line, the traceback goes to the log |

### TUI (`hugin/tui/`)

| Module | Screen or flow |
| --- | --- |
| `app.py` | The Textual app |
| `review.py` | Main screen: tags, summaries, links, editing; `h`, `R`, `n`, settings actions. Largest module, see debt |
| `echo_draft.py` | `h` and `R`: prompt screen (author, profile, length), wait screen, draft creation |
| `news_ideas.py` | `w`: news search, ideas, drafts (author chosen here) |
| `project_settings.py` | Settings screen; editorial rules; author profiles |
| `editor.py` | Markdown editor with front matter fields |
| `tag_manager.py`, `broken_links.py`, `redirects_screen.py`, `affiliates_screen.py` | Their own screens |
| `engine_picker.py`, `post_picker.py` | Modals |

## Where a feature lives

- **Echo drafts (`h`, `R`)**: `review._start_echo_draft` opens `EchoPromptScreen`,
  then `EchoWaitScreen`, which calls `echo.select_samples` / `select_refactor_samples`,
  `echo.build_message`, `echo.write_with_fallback` (Echo, then the system LLM),
  and `echo.create_draft`.
- **New post (`p`)**: `review.action_new_post` → `NewPostScreen` → `writer.create_post`.
- **News ideas (`w`)**: `NewsIdeasScreen` → `news.py` and the LLM → `writer.create_post`.
- **Author list and profiles**: `project.py` (storage), `authors.py` (logic),
  `project_settings.AuthorProfilesScreen` (UI).
- **Settings save**: `project_settings.save_and_sync` writes the file, then
  `git.commit_settings` commits and pushes it on a worker thread.

## Files on the blog (the Hugo site)

- `hugin/config.toml`: summary, links, editorial rules, author profiles (private voice notes).
  Not published: Hugo ignores the folder.
- `data/authors.toml`: the author list, in order. The first one is the default author.
  Hugo can read it as `.Site.Data.authors`.
- `content/<lang>/post/*.md`: posts. The `author` front matter field says who wrote each post.
- Global, on each machine (not in the blog): `~/.hugin/` (engines, API keys, links,
  affiliates, and the legacy project settings still read by a migration).

## Migrations (`hugin/migrations.py`)

Each step checks the current state and does only what is missing. Running
the whole list, or one step, any number of times changes nothing once done.

1. `copy_legacy_settings`: `~/.hugin/projects/<hash>.toml` → `hugin/config.toml`, when the latter is missing.
2. `move_author_list_to_hugo_data`: `names` in `hugin/config.toml` → `data/authors.toml`.
3. `add_post_authors_to_list`: authors found on posts and missing from the list go to its end.
4. `default_author_for_posts`: posts without `author` get the first author of the list.

## Rules the code relies on

- Settings live at the Hugo site root, not in a language folder. `project._project_path`
  and `project.authors_path` both go through `hugo.find_site_root`.
- Settings commits touch only `SETTINGS_PATHS` (`hugin/config.toml`, `data/authors.toml`).
  Posts being edited are never swept into them.
- Startup sync (`git_sync`) commits everything. That is intended, and it is why the
  settings save and the migrations are separate from post edits.
- Author matching is exact: the `author` value must equal the name in the list.
  A post without an author is never a sample for an author-filtered request.
- Echo samples are only published posts (`echo._candidates`); drafts are not used.
- Secrets are never committed: `git.secret_files` refuses `.env`, keys and similar files.
- Persistent writes go through `fsutil.atomic_write_text` or `writer.save_post`.

## Tests

`tests/` mirrors the modules (`test_echo.py`, `test_echo_screens.py`, `test_project.py`,
`test_authors.py`, `test_migrations.py`, `test_settings_sync.py`, ...). Run with
`.venv/bin/python -m pytest -q`. The TUI tests use Textual's `run_test`.

## Known debt

- `tui/review.py` has about 2,600 lines and handles most screens and actions. Split it by
  feature (echo actions, new post, settings, redirects) before adding more to it.
- `linker.strip_internal_links` and `linker.count_internal_links` have no callers.
- The legacy settings fallback (`legacy_settings_path`, read by `load_project`) can go once
  every blog has run step 1 of the migrations.
- The restart path (return code 42) reloads posts without running migrations. This is fine
  because they ran at startup, but a change to the posts on disk during a session will
  not be migrated until the next start.
