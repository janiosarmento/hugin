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
