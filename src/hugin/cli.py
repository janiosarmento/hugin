"""Entrypoint CLI."""

import asyncio
import signal
from datetime import datetime
from pathlib import Path

import click

from hugin.config import load_config
from hugin.embeddings import EmbeddingIndex
from hugin.engines import get_engine
from hugin.log import log_exception
from hugin.hugo import HugoSite, ensure_ignored_in_hugo
from hugin.scanner import AGENT_FILES, Post
from hugin.scanner import (
    collect_tag_pool,
    find_duplicate_tags,
    load_posts,
)
from hugin.state import load_state


def _ignore_ctrl_z() -> None:
    """Keep Ctrl+Z from stopping Hugin outside the TUI.

    Before the TUI starts (git sync, embedding index) and between restarts the
    terminal is in normal mode, so Ctrl+Z sends SIGTSTP and the shell stops
    Hugin mid-startup. While the TUI runs, Textual turns that key into a plain
    keypress. Textual also installs its own SIGTSTP handler and restores normal
    mode on exit, so this is applied again after every app run.
    """
    if hasattr(signal, "SIGTSTP"):
        signal.signal(signal.SIGTSTP, signal.SIG_IGN)


def _git_sync_startup(directory: Path) -> None:
    """On startup: commit local changes, pull --rebase, push. Exit on error."""
    from hugin.git import git_sync

    result = git_sync(directory)
    if not result.success:
        click.echo(f"Git sync failed:\n{result.output}\n")
        click.echo(
            "To resolve conflicts, stash your changes and try again:\n"
            "  git stash && git pull --rebase && git stash pop\n"
            "Then restart hugin."
        )
        raise SystemExit(1)


def _normalize_authors(directory: Path, posts: list[Post]) -> bool:
    """Complete the blog's author list from its posts, then give posts without an author
    the default (the first of the list). True when files changed and must be synced.
    """
    from hugin.authors import add_author_to_posts, default_author, import_authors, post_author
    from hugin.project import load_project, save_project

    settings = load_project(directory)
    added = import_authors(settings.authors, posts)
    if added:
        save_project(directory, settings)
        click.echo(f"Authors found on the posts added to the list: {', '.join(added)}")

    missing = [p for p in posts if not post_author(p)]
    author = default_author(settings.authors, posts)
    if missing and author is None:
        click.echo(
            f"Warning: {len(missing)} post(s) have no author and there is no author list "
            "to take a default from. Add authors in the project settings."
        )
        return bool(added)
    changed = add_author_to_posts(missing, author) if missing else []
    if changed:
        click.echo(f"Default author {author!r} set on {len(changed)} post(s) without one.")
    return bool(added or changed)


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
@click.option("-c", "--clear-cache", is_flag=True, help="Limpar cache de embeddings antes de iniciar.")
def main(
    directory: Path,
    batch: int,
    report: bool,
    engine_id: str | None,
    model_override: str | None,
    clear_cache: bool,
) -> None:
    """hugin: manage Hugo blog posts — tags, summaries, links, and editing."""
    _ignore_ctrl_z()
    directory = directory.resolve()
    config = load_config()

    _git_sync_startup(directory)

    load_problems: list[str] = []
    posts = load_posts(directory, load_problems)
    if not posts:
        for problem in load_problems:
            click.echo(f"Warning: {problem}")
        click.echo("No .md posts found in the directory.")
        raise SystemExit(1)

    # Before the embedding index, so the index reads the posts as they now are
    if _normalize_authors(directory, posts):
        _git_sync_startup(directory)  # commit and push the settings and migrated posts
        posts = load_posts(directory, [])

    # Hugo URL resolution
    site = HugoSite(directory)
    for w in site.warnings:
        click.echo(f"Warning: {w}")

    # Ensure agent instruction files are excluded from Hugo builds
    added = ensure_ignored_in_hugo(directory, sorted(AGENT_FILES))
    for name in added:
        click.echo(f"Added {name} to Hugo ignoreFiles.")

    # Build/update embedding index
    index = EmbeddingIndex(
        posts_dir=directory,
        model_name=config.embeddings.model,
        summary_field=config.frontmatter.summary_field,
    )
    if clear_cache:
        index.clear_cache()
        click.echo("Embedding cache cleared.")
    index.build(posts=posts, url_fn=site.post_url, print_fn=click.echo)

    if report:
        _show_report(posts, directory, index)
        return

    state = load_state(directory)

    all_sorted = sorted(posts, key=lambda p: (p.metadata.get("draft", False), p.date or datetime.min), reverse=True)
    batch_posts = all_sorted if batch == 0 else all_sorted[:batch]

    engine = get_engine(engine_id)
    if model_override:
        engine.model = model_override

    if not engine.available:
        click.echo(
            f"Motor '{engine.id}' sem API key. "
            f"Adicione com: set-secret {engine.id}.api_key"
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
            startup_warnings=load_problems,
        )
        app.run()
        _ignore_ctrl_z()

        if app.return_code == 42:
            # Restart: rebuild everything
            click.echo("Restarting...")
            load_problems = []
            posts = load_posts(directory, load_problems)
            all_sorted = sorted(posts, key=lambda p: (p.metadata.get("draft", False), p.date or datetime.min), reverse=True)
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


@click.command("build-profiles")
@click.argument(
    "directory",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option("--engine", "engine_id", default=None, help="ID do motor de AI.")
@click.option("--force", is_flag=True, help="Regerar mesmo posts que já têm perfil.")
@click.option("--drafts", is_flag=True, help="Incluir posts com draft: true.")
@click.option("-r", "--reset", is_flag=True, help="Apagar todos os link profiles antes de gerar.")
def build_profiles(
    directory: Path,
    engine_id: str | None,
    force: bool,
    drafts: bool,
    reset: bool,
) -> None:
    """Generate link profiles (keywords) for all posts in batch."""
    from hugin.llm import LINK_KEYWORDS_SYSTEM, LINK_KEYWORDS_USER_TEMPLATE, call_llm

    directory = directory.resolve()
    config = load_config()
    engine = get_engine(engine_id)
    posts = load_posts(directory)

    if not drafts:
        posts = [p for p in posts if not p.metadata.get("draft", False)]

    index = EmbeddingIndex(
        posts_dir=directory,
        model_name=config.embeddings.model,
        summary_field=config.frontmatter.summary_field,
    )
    index._load_cache()

    if reset:
        index.clear_keywords()
        click.echo("Link profile cache cleared.")

    to_process = [p for p in posts if force or reset or not index.get_link_keywords(p)]
    already = len(posts) - len(to_process)

    click.echo(f"Posts totais:    {len(posts)}")
    click.echo(f"Já com perfil:   {already}")
    click.echo(f"A gerar:         {len(to_process)}")
    if not to_process:
        click.echo("Nada a fazer.")
        return
    click.echo()

    async def run() -> None:
        errors = 0
        for i, post in enumerate(to_process, 1):
            title = post.metadata.get("title", post.filename)
            click.echo(f"[{i}/{len(to_process)}] {title[:70]}", nl=False)
            try:
                prompt = LINK_KEYWORDS_USER_TEMPLATE.format(
                    title=title,
                    content=post.content[:3000],
                )
                keywords = (await call_llm(engine, prompt, system=LINK_KEYWORDS_SYSTEM)).strip()
                index.store_link_keywords(post, keywords)
                click.echo(f" ✓")
            except Exception as e:  # per-post failure must not stop the batch
                log_exception(f"profile {post.filename}")
                click.echo(f" ✗ {type(e).__name__}: {e}")
                errors += 1

        click.echo()
        click.echo(f"Concluído. {len(to_process) - errors} gerados, {errors} erros.")
        click.echo("Execute 'hugin <dir>' para rebuild dos embeddings com os novos perfis.")

    asyncio.run(run())
