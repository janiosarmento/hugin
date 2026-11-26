"""Generate resized image variants for a Hugo site's static/ directory.

For every raster image under static/, creates `<name>-<W>w.<ext>` for each
width W listed in config/_default/params.toml under
[imageProcessing.content] widths, keeping the original format. Variants that
already exist are left alone, and an image is never upscaled: when the
original is not wider than W, that variant is skipped.

Run it from the site root (or pass the root as an argument):

    hugin-resize [SITE_DIR] [--dry-run] [--jobs N]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn

LIGHT_FORMATS = (".avif", ".webp")  # candidates for a converted image, in tie order
EXTENSIONS = {".avif", ".webp", ".jpg", ".jpeg", ".png", ".gif"}
PART_SUFFIX = ".part"
# Output quality per format; formats not listed use ImageMagick's default.
QUALITY = {".avif": 60, ".webp": 80, ".jpg": 82, ".jpeg": 82}
MAGICK_FORMAT = {".avif": "AVIF", ".webp": "WEBP", ".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".gif": "GIF"}


@dataclass
class Job:
    source: Path
    widths: list[int]  # missing variant widths, not yet checked against the original width


@dataclass
class JobResult:
    source: Path
    created: list[int]
    too_small: list[int]
    error: str | None = None


def read_widths(site: Path) -> list[int]:
    """[imageProcessing.content] widths from the site's params.toml; [] when not set."""
    path = site / "config" / "_default" / "params.toml"
    try:
        with open(path, "rb") as f:
            params = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    widths = params.get("imageProcessing", {}).get("content", {}).get("widths")
    if not isinstance(widths, list) or not all(isinstance(w, int) and w > 0 for w in widths):
        return []
    return sorted(set(widths))


def load_widths(site: Path) -> list[int]:
    """read_widths(), or exit with the reason: the command needs them."""
    widths = read_widths(site)
    if not widths:
        raise SystemExit(f"No valid [imageProcessing.content] widths in {site / 'config/_default/params.toml'}")
    return widths


def variant_path(source: Path, width: int) -> Path:
    return source.with_name(f"{source.stem}-{width}w{source.suffix}")


def is_variant(path: Path, widths: list[int]) -> bool:
    pattern = r"-(?:" + "|".join(str(w) for w in widths) + r")w$"
    return re.search(pattern, path.stem) is not None


def find_jobs(static: Path, widths: list[int]) -> list[Job]:
    jobs = []
    for root, dirs, files in os.walk(static):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            path = Path(root) / name
            if name.startswith(".") or path.suffix.lower() not in EXTENSIONS:
                continue
            if is_variant(path, widths):
                continue
            missing = [w for w in widths if not variant_path(path, w).exists()]
            if missing:
                jobs.append(Job(path, missing))
    jobs.sort(key=lambda j: j.source)
    return jobs


def remove_leftovers(static: Path) -> int:
    """Delete temporary files left behind by an interrupted run."""
    count = 0
    for path in static.rglob(f"*{PART_SUFFIX}"):
        if path.is_file():
            path.unlink()
            count += 1
    return count


def image_width(source: Path) -> int:
    out = subprocess.run(
        ["magick", "identify", "-format", "%w\n", str(source)],
        capture_output=True, text=True, check=True,
    ).stdout
    # Animated images print one line per frame; the first frame is enough.
    return int(out.split()[0])


def _magick(source: Path, out_ext: str, width: int | None, out: Path) -> None:
    """Encode `source` as `out_ext` into `out`, `width` pixels wide (None: its own width)."""
    cmd = ["magick", str(source)]
    if source.suffix.lower() in (".gif", ".webp"):
        cmd.append("-coalesce")  # animated frames must be full before resizing
    if width:
        cmd += ["-resize", f"{width}x"]
    cmd.append("-strip")
    if out_ext in QUALITY:
        cmd += ["-quality", str(QUALITY[out_ext])]
    if out_ext == ".gif":
        cmd += ["-layers", "Optimize"]
    cmd.append(f"{MAGICK_FORMAT[out_ext]}:{out}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"magick exited {result.returncode}")


def _part(path: Path) -> Path:
    return path.with_name(path.name + PART_SUFFIX)


def resize(source: Path, width: int) -> None:
    """Write the variant atomically: a partial file never takes the final name."""
    target = variant_path(source, width)
    tmp = _part(target)
    try:
        _magick(source, source.suffix.lower(), width, tmp)
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)


def write_lightest(source: Path, width: int | None, stem: Path) -> Path:
    """Encode `source` in every LIGHT_FORMATS and keep only the smallest file.

    `stem` is the target path without extension (static/img/cat-800w); the
    winner gets its format's extension. AVIF wins a tie. Returns the file.
    """
    parts = {ext: _part(stem.with_name(stem.name + ext)) for ext in LIGHT_FORMATS}
    try:
        with ThreadPoolExecutor(max_workers=len(parts)) as pool:
            for future in [pool.submit(_magick, source, ext, width, part) for ext, part in parts.items()]:
                future.result()
        best = min(parts, key=lambda ext: parts[ext].stat().st_size)
        target = stem.with_name(stem.name + best)
        parts[best].replace(target)
        return target
    finally:
        for part in parts.values():
            part.unlink(missing_ok=True)


def process(job: Job, dry_run: bool) -> JobResult:
    result = JobResult(job.source, created=[], too_small=[])
    try:
        original = image_width(job.source)
        for width in job.widths:
            if original <= width:
                result.too_small.append(width)
                continue
            if not dry_run:
                resize(job.source, width)
            result.created.append(width)
    except (subprocess.CalledProcessError, RuntimeError, ValueError, IndexError) as exc:
        message = (getattr(exc, "stderr", None) or str(exc)).strip()
        result.error = message.splitlines()[0] if message else repr(exc)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hugin-resize",
        description="Create missing <name>-<W>w variants of every image in a Hugo site's static/.",
    )
    parser.add_argument("site", nargs="?", default=".", help="Hugo site root (default: current directory)")
    parser.add_argument("--dry-run", action="store_true", help="show what would be created, write nothing")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4, help="parallel conversions")
    args = parser.parse_args(argv)

    console = Console()
    site = Path(args.site).expanduser().resolve()
    static = site / "static"
    if not static.is_dir():
        console.print(f"[red]No static/ directory in {site}[/red]")
        return 1
    widths = load_widths(site)

    if not args.dry_run:
        leftovers = remove_leftovers(static)
        if leftovers:
            console.print(f"Removed {leftovers} partial file(s) from an interrupted run.")

    jobs = find_jobs(static, widths)
    console.print(f"Widths: {', '.join(str(w) for w in widths)} — {len(jobs)} image(s) missing variants.")
    if not jobs:
        return 0

    results: list[JobResult] = []
    columns = (
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        TextColumn("{task.fields[current]}", style="dim"),
    )
    with Progress(*columns, console=console) as progress:
        task = progress.add_task("Checking" if args.dry_run else "Resizing", total=len(jobs), current="")
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            futures = {pool.submit(process, job, args.dry_run): job for job in jobs}
            try:
                for future in as_completed(futures):
                    res = future.result()
                    results.append(res)
                    progress.update(task, advance=1, current=str(res.source.relative_to(static)))
            except KeyboardInterrupt:
                pool.shutdown(wait=True, cancel_futures=True)
                console.print("[yellow]Interrupted; finished variants were kept.[/yellow]")
                return 130

    created = sum(len(r.created) for r in results)
    small = sum(len(r.too_small) for r in results)
    errors = [r for r in results if r.error]
    verb = "Would create" if args.dry_run else "Created"
    console.print(f"{verb} {created} variant(s); skipped {small} (original not wider than the target).")
    if args.dry_run:
        for r in sorted(results, key=lambda r: r.source):
            for w in r.created:
                console.print(f"  {variant_path(r.source, w).relative_to(static)}")
    for r in errors:
        console.print(f"[red]Failed:[/red] {r.source.relative_to(static)}: {r.error}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
