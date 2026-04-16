"""Munin CLI entry point."""

import math
from datetime import datetime
from pathlib import Path

import click

from hugin.engines import get_engine
from hugin.scanner import load_posts

from munin.config import load_config
from munin.embeddings import EmbeddingIndex
from munin.hugo import HugoSite


@click.command()
@click.argument(
    "directory",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option("--batch", default=0, help="Limit post list to N most recent (0 = all).")
@click.option("--report", is_flag=True, help="Print embedding cache stats and exit.")
@click.option("--engine", "engine_id", default=None, help="Override engine ID.")
def main(
    directory: Path,
    batch: int,
    report: bool,
    engine_id: str | None,
) -> None:
    """Munin: manage internal links in Hugo blog posts."""
    directory = directory.resolve()
    config = load_config()

    posts = load_posts(directory)
    if not posts:
        click.echo("No .md posts found in directory.")
        raise SystemExit(1)

    # Hugo URL resolution
    site = HugoSite(directory)
    for w in site.warnings:
        click.echo(f"Warning: {w}")

    # Build/update embedding index (with progress output)
    index = EmbeddingIndex(
        posts_dir=directory,
        model_name=config.embeddings.model,
        summary_field=config.frontmatter.summary_field,
    )
    index.build(
        posts=posts,
        url_fn=site.post_url,
        print_fn=click.echo,
    )

    if report:
        _show_report(index, posts)
        return

    # Sort by date, newest first
    all_sorted = sorted(posts, key=lambda p: p.date or datetime.min, reverse=True)
    batch_posts = all_sorted if batch == 0 else all_sorted[:batch]

    engine = get_engine(engine_id)
    if not engine.available:
        click.echo(
            f"Engine '{engine.id}' has no API key. "
            f"Set {engine.id.upper()}_API_KEY in your environment."
        )
        raise SystemExit(1)

    # Launch TUI
    from munin.tui import MuninApp

    app = MuninApp(
        posts=batch_posts,
        all_posts=posts,
        engine=engine,
        config=config,
        site=site,
        index=index,
    )
    app.run()


def _show_report(index: EmbeddingIndex, posts: list) -> None:
    cached = index._cache.get("posts", {})
    total = len(posts)
    cached_count = len(cached)

    click.echo(f"Total posts:    {total}")
    click.echo(f"Cached:         {cached_count}")
    click.echo(f"Missing:        {total - cached_count}")
