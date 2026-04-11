"""Entrypoint CLI."""

from datetime import datetime
from pathlib import Path

import click

from hugin.engines import get_engine, load_engines
from hugin.scanner import (
    collect_tag_pool,
    find_duplicate_tags,
    load_posts,
    prioritize,
)
from hugin.state import load_state


@click.command()
@click.argument(
    "directory",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option("--batch", default=0, help="Máximo de posts a processar (0 = todos).")
@click.option("--report", is_flag=True, help="Exibir estatísticas sem chamar LLM.")
@click.option("--engine", "engine_id", default=None, help="ID do motor de AI.")
@click.option("--model", "model_override", default=None, help="Override do modelo.")
def main(
    directory: Path,
    batch: int,
    report: bool,
    engine_id: str | None,
    model_override: str | None,
) -> None:
    """hugin: gera tags para posts Hugo via LLM."""
    directory = directory.resolve()
    posts = load_posts(directory)

    if not posts:
        click.echo("Nenhum post .md encontrado no diretório.")
        raise SystemExit(1)

    if report:
        _show_report(posts, directory)
        return

    state = load_state(directory)

    # All posts, sorted by publication date (newest first)
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

    # Importar e lançar TUI
    from hugin.tui.app import HuginApp

    app = HuginApp(
        posts=batch_posts,
        all_posts=posts,
        engine=engine,
        pool=pool,
        state=state,
        directory=directory,
    )
    app.run()


def _show_report(posts: list, directory: Path) -> None:
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
    click.echo(f"Posts editados após tags:    {edited}")
    click.echo(f"Posts atualizados:           {up_to_date}")
    click.echo(f"Total:                       {len(posts)}")
    click.echo()
    click.echo(f"Tags únicas:                 {len(pool)}")

    if duplicates:
        click.echo()
        click.echo("Tags possivelmente duplicadas:")
        for tag_a, tag_b, reason in duplicates:
            click.echo(f"  {tag_a} ↔ {tag_b}   ({reason})")
